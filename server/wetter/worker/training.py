"""Nächtliches Training der Vorhersagemodelle.

Der Ablauf folgt den drei Stufen aus dem Entwurf:

1. **Grundmodell auf DWD-Daten.** Jahrzehnte echter Messungen der nächstgelegenen
   Station -- der Grund, warum das System am ersten Tag etwas kann und nicht erst
   nach zwei Jahren.
2. **Bias-Korrektur**, sobald genug eigene Daten vorliegen. Gleicht den
   Aufstellungsunterschied aus.
3. **Feintuning auf eigenen Daten**, sobald es sich lohnt. LightGBM kann ein
   bestehendes Modell weitertrainieren (``init_model``), statt bei null anzufangen.

Neue Modelle gehen immer in den Schattenbetrieb. Die Entscheidung, ob sie das aktive
ablösen, trifft :mod:`wetter.worker.promotion` anhand der Verifikation -- nicht dieser
Job und nicht der Umstand, dass ein Modell neuer ist.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from wetter.db.models import Station
from wetter.dwd.store import load_dwd_hourly
from wetter.features.build import build_features
from wetter.features.climatology import Climatology
from wetter.features.targets import RAIN_LEADS, build_targets
from wetter.models.bias import apply_all, fit_all
from wetter.models.registry import save_rain_model, save_temp_model
from wetter.models.split import TimeSplit
from wetter.models.train import train_rain_model, train_temp_model
from wetter.worker.forecast import HOURLY_COLUMNS

log = logging.getLogger(__name__)

#: Soviele eigene Stunden müssen vorliegen, bevor auf ihnen feinjustiert wird.
#: Ein halbes Jahr deckt zwei Jahreszeiten ab; darunter lernte das Modell vor allem,
#: dass es Sommer ist.
MIN_OWN_HOURS_FINETUNE = 4380

#: Merkmale, die es nur bei der eigenen Station gibt. Beim Training auf DWD-Daten
#: sind sie durchgehend leer und würden das Modell nur aufblähen.
OWN_ONLY_FEATURES = ("lightning_count_1h", "lightning_min_distance_km")


@dataclass
class TrainingReport:
    """Was ein Trainingslauf bewirkt hat."""

    dwd_hours: int = 0
    own_hours: int = 0
    finetuned: bool = False
    bias_columns: list[str] = field(default_factory=list)
    models: list[int] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        teile = [
            f"{self.dwd_hours} DWD-Stunden",
            f"{self.own_hours} eigene Stunden",
            f"{len(self.models)} Modelle",
        ]
        if self.finetuned:
            teile.append("feinjustiert")
        if self.bias_columns:
            teile.append(f"Bias-Korrektur für {', '.join(self.bias_columns)}")
        return ", ".join(teile)


def load_own_hourly(session: Session, station: Station) -> pd.DataFrame:
    """Liest die eigenen Stundenwerte auf lückenlosem Raster."""
    from sqlalchemy import select

    from wetter.db.models import Hourly

    spalten = [Hourly.time, *[getattr(Hourly, c) for c in HOURLY_COLUMNS]]
    rows = session.execute(
        select(*spalten)
        .where(Hourly.station_id == station.id)
        .order_by(Hourly.time)
    ).all()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=["time", *HOURLY_COLUMNS])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.set_index("time").sort_index()
    raster = pd.date_range(frame.index.min(), frame.index.max(), freq="h", tz="UTC")
    return frame.reindex(raster).rename_axis("time")


def train_for_station(
    session: Session,
    station: Station,
    *,
    root: Path | str,
    rain_leads: tuple[int, ...] = RAIN_LEADS,
    temp_leads: tuple[int, ...] = (6, 12, 24, 48),
    num_boost_round: int = 400,
    min_own_hours: int = MIN_OWN_HOURS_FINETUNE,
) -> TrainingReport:
    """Trainiert alle Modelle für eine Station und legt sie im Schatten ab."""
    bericht = TrainingReport()

    dwd = load_dwd_hourly(session)
    if dwd.empty:
        bericht.skipped.append("keine DWD-Daten abgelegt -- Bootstrap fehlt")
        return bericht
    bericht.dwd_hours = len(dwd)

    eigene = load_own_hourly(session, station)
    bericht.own_hours = len(eigene)

    # Bias-Korrektur lernen, solange beide Reihen sich überlappen. Korrigiert wird
    # die *eigene* Reihe auf die DWD-Skala -- das Modell denkt in DWD-Werten.
    abbildungen = {}
    if not eigene.empty:
        gemeinsam = [c for c in eigene.columns if c in dwd.columns]
        abbildungen = fit_all(eigene, dwd, columns=gemeinsam)
        bericht.bias_columns = sorted(abbildungen)
        if abbildungen:
            eigene = apply_all(eigene, abbildungen)

    klimatologie = Climatology.from_hourly(dwd)
    merkmale = _features(dwd, station, klimatologie)
    ziele = build_targets(dwd)
    split = TimeSplit.by_fraction(merkmale.index)

    eigene_merkmale = None
    eigene_ziele = None
    if bericht.own_hours >= min_own_hours:
        eigene_merkmale = _features(eigene, station, klimatologie)
        eigene_ziele = build_targets(eigene)
        bericht.finetuned = True
    elif bericht.own_hours:
        bericht.skipped.append(
            f"nur {bericht.own_hours} eigene Stunden, Feintuning ab {min_own_hours}"
        )

    zeitraum = {
        "train_start": merkmale.index[0].to_pydatetime(),
        "train_end": merkmale.index[-1].to_pydatetime(),
        "source": "mixed" if bericht.finetuned else "dwd",
    }

    for lead in rain_leads:
        spalte = f"rain_next_{lead}h"
        if spalte not in ziele:
            bericht.skipped.append(f"{spalte}: kein Ziel in den DWD-Daten")
            continue
        modell = train_rain_model(
            merkmale,
            ziele[spalte],
            lead_hours=lead,
            split=split,
            num_boost_round=num_boost_round,
        )
        if eigene_merkmale is not None and spalte in eigene_ziele:
            modell = _finetune_rain(
                modell, eigene_merkmale, eigene_ziele[spalte], lead=lead
            )
        ref = save_rain_model(session, modell, root=root, **zeitraum)
        bericht.models.append(ref.id)

    for lead in temp_leads:
        spalte = f"temp_at_{lead}h"
        if spalte not in ziele:
            bericht.skipped.append(f"{spalte}: kein Ziel in den DWD-Daten")
            continue
        modell = train_temp_model(
            merkmale,
            ziele[spalte],
            lead_hours=lead,
            split=split,
            num_boost_round=num_boost_round,
        )
        ref = save_temp_model(session, modell, root=root, **zeitraum)
        bericht.models.append(ref.id)

    log.info("Training für %s: %s", station.key, bericht.summary())
    return bericht


def _features(
    frame: pd.DataFrame, station: Station, climatology: Climatology
) -> pd.DataFrame:
    merkmale = build_features(
        frame,
        latitude=station.latitude,
        longitude=station.longitude,
        altitude_m=station.altitude_m,
        climatology=climatology,
    )
    # Blitzmerkmale kennt der DWD nicht; sie blieben durchgehend leer und trügen
    # nichts bei. Sobald der AS3935 läuft, kommen sie über ein eigenes Modell dazu.
    return merkmale.drop(columns=list(OWN_ONLY_FEATURES), errors="ignore")


def _finetune_rain(modell, merkmale: pd.DataFrame, ziel: pd.Series, *, lead: int):
    """Trainiert ein DWD-Modell auf eigenen Daten weiter.

    Bewusst mit wenigen zusätzlichen Runden und kleiner Lernrate: die eigene Reihe
    ist um Grössenordnungen kürzer als die DWD-Reihe. Wer hier kräftig nachtrainiert,
    wirft das Gelernte weg und ersetzt es durch die Wetterlage der letzten Monate.
    """
    import lightgbm as lgb

    from wetter.models.train import LGB_DEFAULTS

    gueltig = ziel.notna().to_numpy()
    x = merkmale.loc[gueltig].reindex(columns=modell.feature_names)
    y = ziel.to_numpy(dtype=float)[gueltig]
    if len(x) < 500:
        return modell

    params = {**LGB_DEFAULTS, "objective": "binary", "learning_rate": 0.01}
    modell.booster = lgb.train(
        params,
        lgb.Dataset(x, label=y, feature_name=modell.feature_names),
        num_boost_round=50,
        init_model=modell.booster,
    )
    modell.metrics = {**modell.metrics, "finetuned_on": len(x)}
    log.info("Regenmodell %d h auf %d eigenen Stunden feinjustiert", lead, len(x))
    return modell


def last_training(session: Session) -> datetime | None:
    """Zeitpunkt des letzten Trainingslaufs."""
    from sqlalchemy import func, select

    from wetter.db.models import Model

    return session.scalar(select(func.max(Model.trained_at)))


def needs_training(session: Session, *, now: datetime | None = None) -> bool:
    """Ist seit dem letzten Lauf mehr als ein Tag vergangen?"""
    letzte = last_training(session)
    if letzte is None:
        return True
    jetzt = now or datetime.now(UTC)
    if letzte.tzinfo is None:
        letzte = letzte.replace(tzinfo=UTC)
    return (jetzt - letzte).total_seconds() > 23 * 3600
