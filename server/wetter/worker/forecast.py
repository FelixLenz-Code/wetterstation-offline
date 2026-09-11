"""Vorhersagen rechnen und ablegen.

Läuft im Takt von Minuten, nicht von Stunden: die Merkmale ändern sich mit jedem
neuen Stundenwert, und eine Drucktendenz, die zwei Stunden alt ist, ist für eine
Sechs-Stunden-Vorhersage bereits ein Drittel veraltet.

Abgelegt wird **jede** Vorhersage, auch wenn sie nie jemand ansieht. Ohne die Ablage
lässt sich später nicht mehr sagen, ob das Modell recht hatte -- und genau das ist
der Unterschied zwischen einer Vorhersage und einer Behauptung.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wetter.db.models import Forecast, Hourly, Station
from wetter.features.build import build_features
from wetter.features.climatology import Climatology
from wetter.models.registry import (
    STATUS_ACTIVE,
    STATUS_SHADOW,
    ModelRef,
    list_models,
    load_rain_model,
    load_temp_model,
    missing_features,
)

log = logging.getLogger(__name__)

#: Soviel Vorgeschichte braucht der Merkmalsbau: die längste Tendenz geht über
#: 24 Stunden, dazu etwas Reserve für Lücken.
HISTORY_HOURS = 72

#: Spalten, die aus der Stundentabelle in den Merkmalsbau gehen.
HOURLY_COLUMNS: tuple[str, ...] = (
    "temperature_c",
    "humidity_pct",
    "pressure_station_hpa",
    "pressure_sea_hpa",
    "dewpoint_c",
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_dir_deg",
    "precip_mm",
    "cloud_cover_okta",
    "sky_temp_c",
    "global_radiation_wm2",
    "lightning_count",
    "lightning_distance_km",
)


def load_hourly(
    session: Session, station: Station, *, end: datetime, hours: int = HISTORY_HOURS
) -> pd.DataFrame:
    """Liest die letzten Stundenwerte auf einem lückenlosen Raster.

    Lückenlos ist keine Feinheit: der Merkmalsbau bildet Tendenzen über Zeilen, nicht
    über Zeitstempel. Fehlte eine Stunde, wäre "vor drei Stunden" in Wahrheit vier
    Stunden her, und die Drucktendenz -- das wichtigste Merkmal überhaupt -- wäre
    still falsch.
    """
    start = end - timedelta(hours=hours)
    spalten = [Hourly.time, *[getattr(Hourly, c) for c in HOURLY_COLUMNS]]
    rows = session.execute(
        select(*spalten)
        .where(
            Hourly.station_id == station.id,
            Hourly.time >= start,
            Hourly.time <= end,
        )
        .order_by(Hourly.time)
    ).all()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=["time", *HOURLY_COLUMNS])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.set_index("time").sort_index()

    raster = pd.date_range(
        frame.index.min().floor("h"), frame.index.max().floor("h"), freq="h", tz="UTC"
    )
    return frame.reindex(raster)


def write_forecasts(
    session: Session,
    station: Station,
    rows: list[dict],
) -> int:
    """Legt Vorhersagen ab. Ein erneuter Lauf ersetzt die vorige Rechnung.

    Derselbe Zeitpunkt kann mehrfach gerechnet werden, etwa weil Nachzügler aus dem
    Ringpuffer eine Stunde vervollständigt haben. Dann gilt die spätere Rechnung --
    sie beruht auf mehr Daten.
    """
    if not rows:
        return 0
    stmt = insert(Forecast).values(rows)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[
                Forecast.model_id,
                Forecast.issued_at,
                Forecast.valid_at,
                Forecast.target,
            ],
            set_={"value": stmt.excluded.value, "quantiles": stmt.excluded.quantiles},
        )
    )
    return len(rows)


def run_forecasts(
    session: Session,
    station: Station,
    *,
    climatology: Climatology | None,
    issued_at: datetime | None = None,
    include_shadow: bool = True,
) -> tuple[int, list[str]]:
    """Rechnet alle aktiven (und optional die Schatten-) Modelle durch.

    Gibt die Zahl der abgelegten Vorhersagen und Hinweise zurück, die in die
    Oberfläche gehören -- etwa dass einem Modell Merkmale fehlen.
    """
    jetzt = (issued_at or datetime.now(UTC)).replace(minute=0, second=0, microsecond=0)
    stunden = load_hourly(session, station, end=jetzt)
    if stunden.empty or len(stunden) < 2:
        return 0, ["zu wenige Stundenwerte für eine Vorhersage"]

    merkmale = build_features(
        stunden,
        latitude=station.latitude,
        longitude=station.longitude,
        altitude_m=station.altitude_m,
        climatology=climatology,
    )
    # Gerechnet wird immer auf der jüngsten vollständigen Zeile.
    letzte = merkmale.iloc[[-1]]
    ausgestellt = letzte.index[-1].to_pydatetime()

    stati = [STATUS_ACTIVE] + ([STATUS_SHADOW] if include_shadow else [])
    hinweise: list[str] = []
    zeilen: list[dict] = []

    for status in stati:
        for ref in list_models(session, status=status):
            fehlend = missing_features(ref, list(merkmale.columns))
            if fehlend:
                # Kein Abbruch: LightGBM kommt mit fehlenden Werten zurecht. Aber es
                # gehört ins Log, denn ein Modell, dem die Hälfte seiner Merkmale
                # fehlt, sagt nicht mehr das voraus, wofür es trainiert wurde.
                hinweise.append(
                    f"Modell {ref.id} ({ref.target}, {ref.lead_hours} h): "
                    f"{len(fehlend)} Merkmale fehlen"
                )
            zeile = _predict_one(session, station, ref, letzte, ausgestellt)
            if zeile is not None:
                zeilen.append(zeile)

    anzahl = write_forecasts(session, station, zeilen)
    log.info(
        "%s: %d Vorhersagen ausgestellt für %s",
        station.key,
        anzahl,
        ausgestellt.isoformat(timespec="hours"),
    )
    return anzahl, hinweise


def _predict_one(
    session: Session,
    station: Station,
    ref: ModelRef,
    features: pd.DataFrame,
    issued_at: datetime,
) -> dict | None:
    """Rechnet ein einzelnes Modell und formt die Datenbankzeile."""
    gueltig = issued_at + timedelta(hours=ref.lead_hours)
    # Fehlende Merkmale als NaN ergänzen, damit die Spaltenreihenfolge des Modells
    # erhalten bleibt -- ein später dazugekommener Sensor darf sie nicht verschieben.
    eingabe = features.reindex(columns=ref.feature_names)

    try:
        if ref.target == "rain":
            modell = load_rain_model(session, ref.id)
            wert = float(modell.predict(eingabe)[0])
            return {
                "model_id": ref.id,
                "station_id": station.id,
                "issued_at": issued_at,
                "valid_at": gueltig,
                "target": "rain",
                "value": wert,
                "quantiles": None,
            }
        if ref.target == "temperature":
            modell = load_temp_model(session, ref.id)
            q = modell.predict(eingabe)
            median = q.get(0.5)
            return {
                "model_id": ref.id,
                "station_id": station.id,
                "issued_at": issued_at,
                "valid_at": gueltig,
                "target": "temperature",
                "value": float(median[0]) if median is not None else None,
                "quantiles": {str(k): float(v[0]) for k, v in q.items()},
            }
    except Exception:
        # Ein kaputtes Modell darf nicht die übrigen mitreissen.
        log.exception("Modell %d konnte nicht rechnen", ref.id)
        return None

    log.warning("Unbekanntes Ziel %r in Modell %d", ref.target, ref.id)
    return None
