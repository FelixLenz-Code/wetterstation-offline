"""Rohmessungen zu Stundenwerten verdichten.

Das Bindeglied der ganzen Kette: Merkmalsbau, Training und Vorhersage arbeiten
ausschliesslich auf Stundenwerten im kanonischen Schema -- und zwar auf demselben,
in dem auch die DWD-Daten liegen. Erst dadurch ist ein auf DWD trainiertes Modell
auf der eigenen Station anwendbar.

Zwei Dinge passieren hier, die leicht falsch laufen:

* **Der Testmodus wirkt vor der Aggregation.** Ein Sensor, der zum Entwickeln drinnen
  lag, darf nicht ueber den Stundenmittelwert doch noch ins Training sickern.
* **Windrichtung wird als Vektor gemittelt.** Der arithmetische Mittelwert von 350
  und 10 Grad ist 180 Grad -- also genau die Gegenrichtung. Bei Nordwind wuerde die
  Station Suedwind melden, und das Merkmal "Winddrehung", einer der staerksten
  Anzeiger fuer einen Frontdurchgang, waere systematisch falsch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wetter.db.models import Hourly, Measurement, Station
from wetter.db.sensors import StateInterval, mask_by_state
from wetter.dwd.store import chunk_size
from wetter.features import meteo

log = logging.getLogger(__name__)

#: Wie jede Messgroesse ueber die Stunde zusammengefasst wird.
#: Die Wahl ist nicht beliebig: Niederschlag summiert sich, eine Boe ist per
#: Definition ein Maximum, und die kleinste Blitzentfernung ist die gefaehrliche.
AGGREGATION: dict[str, str] = {
    "temperature_c": "mean",
    "humidity_pct": "mean",
    "pressure_station_hpa": "mean",
    "wind_speed_ms": "mean",
    "wind_gust_ms": "max",
    "precip_mm": "sum",
    "sky_temp_c": "mean",
    "lightning_count": "sum",
    "lightning_distance_km": "min",
    "illuminance_lux": "mean",
    "global_radiation_wm2": "mean",
}

#: Mindestens so viele Rohmessungen muss eine Stunde haben, damit sie gilt.
#: Eine Stunde aus zwei Werten ist etwas anderes als eine aus 360 -- vor allem beim
#: Niederschlag, wo eine Luecke die Summe stillschweigend zu klein macht.
MIN_SAMPLES = 2


def circular_mean_direction(
    direction_deg: pd.Series, speed_ms: pd.Series | None = None
) -> float:
    """Mittlere Windrichtung als Vektormittel in Grad.

    Ist die Geschwindigkeit bekannt, wird nach ihr gewichtet: die Richtung bei
    Windstille ist bedeutungslos und soll das Ergebnis nicht mitbestimmen.

    Gibt ``NaN`` zurueck, wenn sich die Richtungen aufheben -- bei einer Stunde mit
    Wind aus allen Richtungen *gibt* es keine mittlere Richtung, und eine erfundene
    waere schlimmer als keine.
    """
    gueltig = direction_deg.notna()
    if speed_ms is not None:
        gueltig &= speed_ms.notna()
    if not gueltig.any():
        return float("nan")

    richtung = direction_deg[gueltig]
    if speed_ms is not None:
        betrag = speed_ms[gueltig]
    else:
        betrag = pd.Series(1.0, index=richtung.index)
    u, v = meteo.wind_components(betrag, richtung)

    u_mittel = float(np.mean(u))
    v_mittel = float(np.mean(v))
    # Heben sich die Vektoren nahezu auf, ist die Richtung nicht bestimmbar.
    if np.hypot(u_mittel, v_mittel) < 1e-6:
        return float("nan")
    return float(meteo.wind_direction(u_mittel, v_mittel))


def aggregate_hourly(
    frame: pd.DataFrame,
    *,
    altitude_m: float,
    min_samples: int = MIN_SAMPLES,
) -> pd.DataFrame:
    """Verdichtet bereits maskierte Rohmessungen zu Stundenwerten.

    ``frame`` hat einen zeitzonenbewussten Index und die Spalten aus
    :data:`AGGREGATION`. Ergebnis ist das kanonische Stundenschema inklusive der
    abgeleiteten Groessen Taupunkt und Meeresniveaudruck.
    """
    if frame.empty:
        return pd.DataFrame()

    gruppen = frame.groupby(frame.index.floor("h"))

    teile: dict[str, pd.Series] = {}
    for spalte, wie in AGGREGATION.items():
        if spalte in frame.columns:
            teile[spalte] = gruppen[spalte].agg(wie)

    out = pd.DataFrame(teile)
    out.index.name = "time"

    # Windrichtung getrennt: der Gruppierer kann kein Vektormittel.
    if "wind_dir_deg" in frame.columns:
        speed = frame["wind_speed_ms"] if "wind_speed_ms" in frame.columns else None
        out["wind_dir_deg"] = pd.Series(
            {
                stunde: circular_mean_direction(
                    teil["wind_dir_deg"],
                    teil["wind_speed_ms"] if speed is not None else None,
                )
                for stunde, teil in frame.groupby(frame.index.floor("h"))
            }
        )

    # Zaehlt nur Zeilen, in denen ueberhaupt etwas gemessen wurde.
    out["sample_count"] = gruppen.size().astype(int)

    # Duenn besetzte Stunden verwerfen -- aber nur die Messwerte, nicht die Zeile:
    # die Zahl der Stichproben bleibt sichtbar.
    zu_duenn = out["sample_count"] < min_samples
    if zu_duenn.any():
        messspalten = [c for c in out.columns if c != "sample_count"]
        out.loc[zu_duenn, messspalten] = np.nan

    # Abgeleitete Groessen, damit das Schema dem der DWD-Daten entspricht.
    if {"temperature_c", "humidity_pct"} <= set(out.columns):
        out["dewpoint_c"] = meteo.dewpoint(out["temperature_c"], out["humidity_pct"])
    if "pressure_station_hpa" in out.columns and "temperature_c" in out.columns:
        out["pressure_sea_hpa"] = meteo.to_sea_level(
            out["pressure_station_hpa"],
            altitude_m,
            out["temperature_c"],
            out.get("humidity_pct"),
        )
    return out


def load_measurements(
    session: Session,
    station: Station,
    start: datetime,
    end: datetime,
) -> pd.DataFrame:
    """Liest Rohmessungen im Zeitraum ``[start, end)`` als DataFrame."""
    spalten = [Measurement.time, *[getattr(Measurement, c) for c in AGGREGATION]]
    if hasattr(Measurement, "wind_dir_deg"):
        spalten.append(Measurement.wind_dir_deg)

    rows = session.execute(
        select(*spalten)
        .where(
            Measurement.station_id == station.id,
            Measurement.time >= start,
            Measurement.time < end,
        )
        .order_by(Measurement.time)
    ).all()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=[c.name for c in spalten])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame.set_index("time")


def write_hourly(session: Session, station: Station, frame: pd.DataFrame) -> int:
    """Schreibt Stundenwerte per Upsert.

    Eine Stunde wird neu berechnet, sobald neue Rohdaten fuer sie eintreffen -- etwa
    weil die Station nach einem Ausfall aufholt. Deshalb wird die Zeile vollstaendig
    ersetzt und nicht ergaenzt: der neue Wert ist aus mehr Daten gerechnet und damit
    in jedem Fall der bessere.
    """
    if frame.empty:
        return 0

    spalten = [c.name for c in Hourly.__table__.columns]
    zeilen = []
    for zeit, reihe in frame.iterrows():
        zeile = {"station_id": station.id, "time": zeit.to_pydatetime()}
        for c in spalten:
            if c in ("station_id", "time") or c not in frame.columns:
                continue
            wert = reihe[c]
            zeile[c] = None if pd.isna(wert) else float(wert)
        zeile["sample_count"] = int(reihe.get("sample_count", 0) or 0)
        zeilen.append(zeile)

    # Blockweise schreiben: eine Postgres-Anweisung darf höchstens 65.535 Parameter
    # tragen. Im laufenden Betrieb sind es nur ein paar Stunden auf einmal, aber ein
    # Nachrechnen über Monate würde die Grenze sonst reissen -- und zwar erst beim
    # Nutzer, weil im Test niemand Monate nachrechnet.
    block = chunk_size(len(spalten))
    for start in range(0, len(zeilen), block):
        teil = zeilen[start : start + block]
        stmt = insert(Hourly).values(teil)
        aktualisiere = {
            c: getattr(stmt.excluded, c)
            for c in spalten
            if c not in ("station_id", "time")
        }
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[Hourly.station_id, Hourly.time], set_=aktualisiere
            )
        )
    return len(zeilen)


def rollup(
    session: Session,
    station: Station,
    intervals: list[StateInterval],
    *,
    start: datetime,
    end: datetime,
    keep_test: bool = False,
) -> int:
    """Verdichtet einen Zeitraum und schreibt ihn weg.

    Die Maskierung laeuft vor der Aggregation. Wuerde man erst mitteln und dann
    maskieren, waere ein Stundenwert schon mit Testdaten verunreinigt, und die
    Verunreinigung liesse sich nicht mehr herausrechnen.
    """
    roh = load_measurements(session, station, start, end)
    if roh.empty:
        return 0

    sauber = mask_by_state(roh, intervals, keep_test=keep_test)
    stunden = aggregate_hourly(sauber, altitude_m=station.altitude_m)
    anzahl = write_hourly(session, station, stunden)
    log.info(
        "%s: %d Stundenwerte aus %d Rohmessungen (%s bis %s)",
        station.key,
        anzahl,
        len(roh),
        start.isoformat(timespec="minutes"),
        end.isoformat(timespec="minutes"),
    )
    return anzahl


def pending_range(
    session: Session, station: Station, *, lookback_hours: int = 6
) -> tuple[datetime, datetime] | None:
    """Bestimmt, welcher Zeitraum neu verdichtet werden muss.

    Die letzten Stunden werden absichtlich noch einmal gerechnet: Nachzuegler aus dem
    Ringpuffer der Station koennen eine laengst geschriebene Stunde nachtraeglich
    vervollstaendigen.
    """
    letzte = session.scalar(
        select(Hourly.time)
        .where(Hourly.station_id == station.id)
        .order_by(Hourly.time.desc())
        .limit(1)
    )
    erste_rohe = session.scalar(
        select(Measurement.time)
        .where(Measurement.station_id == station.id)
        .order_by(Measurement.time)
        .limit(1)
    )
    letzte_rohe = session.scalar(
        select(Measurement.time)
        .where(Measurement.station_id == station.id)
        .order_by(Measurement.time.desc())
        .limit(1)
    )
    if erste_rohe is None or letzte_rohe is None:
        return None

    start = erste_rohe if letzte is None else letzte - timedelta(hours=lookback_hours)
    start = max(start, erste_rohe)
    # Die laufende Stunde ist noch unvollstaendig und wird erst spaeter gerechnet.
    ende = letzte_rohe.replace(minute=0, second=0, microsecond=0)
    return (start, ende) if start < ende else None
