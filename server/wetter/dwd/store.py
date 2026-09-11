"""DWD-Bootstrap in der Datenbank ablegen und wieder lesen.

Der Import dauert eine knappe Minute und lädt für einen Standort leicht 600.000
Stunden. Das will man nicht bei jedem Trainingslauf wiederholen -- schon gar nicht,
wenn gerade kein Internet da ist, denn genau dann soll das System ja weiterlaufen.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wetter.db.models import DwdHourly, DwdStation
from wetter.dwd.bootstrap import BootstrapResult

log = logging.getLogger(__name__)

#: Spalten der Tabelle, die aus dem Bootstrap kommen können.
DWD_COLUMNS: tuple[str, ...] = tuple(
    c.name for c in DwdHourly.__table__.columns if c.name != "time"
)

#: Harte Grenze des Postgres-Protokolls: eine Anweisung darf höchstens 65.535
#: Parameter tragen. Das ist keine Einstellung, sondern ein 16-Bit-Feld im
#: Wire-Protokoll.
MAX_PARAMS = 65535

#: Sicherheitsabstand, damit zusätzliche Parameter (etwa aus einem ON CONFLICT mit
#: Bedingung) nicht über die Grenze schieben.
PARAM_MARGIN = 0.9


#: Name der temporären Übergabetabelle. Sie verschwindet mit dem Commit.
TEMP_TABLE = "dwd_hourly_import"


def chunk_size(columns: int) -> int:
    """Zeilen je Anweisung, abgeleitet aus der Spaltenzahl.

    Eine feste Zahl ist hier eine Falle: 5000 Zeilen sind bei fünf Spalten
    unauffällig und sprengen bei zwanzig das Parameterlimit. Der Fehler zeigt sich
    dann auch nicht im Test mit einer kurzen Kunstreihe, sondern erst beim ersten
    echten Bootstrap mit allen Messgrößen -- und der läuft beim Nutzer, nicht auf
    dem Entwicklungsrechner.
    """
    if columns <= 0:
        return 1
    return max(1, int(MAX_PARAMS * PARAM_MARGIN) // columns)


def save_bootstrap(session: Session, result: BootstrapResult) -> int:
    """Schreibt Stundenwerte und Stationsauswahl weg."""
    session.execute(
        insert(DwdStation)
        .values(
            [
                {
                    "dataset_key": key,
                    "station_id": cand.station.station_id,
                    "name": cand.station.name,
                    "latitude": cand.station.latitude,
                    "longitude": cand.station.longitude,
                    "altitude_m": cand.station.altitude_m,
                    "distance_km": cand.distance_km,
                    "imported_at": datetime.now(UTC),
                    "first_time": result.frame.index.min().to_pydatetime()
                    if len(result.frame)
                    else None,
                    "last_time": result.frame.index.max().to_pydatetime()
                    if len(result.frame)
                    else None,
                }
                for key, cand in result.stations.items()
            ]
        )
        .on_conflict_do_update(
            index_elements=[DwdStation.dataset_key],
            set_={
                c: getattr(insert(DwdStation).excluded, c)
                for c in (
                    "station_id",
                    "name",
                    "latitude",
                    "longitude",
                    "altitude_m",
                    "distance_km",
                    "imported_at",
                    "first_time",
                    "last_time",
                )
            },
        )
    )

    return _copy_hourly(session, result.frame)


def _copy_hourly(session: Session, frame: pd.DataFrame) -> int:
    """Schreibt die Stundenwerte per COPY über eine temporäre Tabelle.

    Der naheliegende Weg -- Zeilen in Python zusammenbauen und als INSERT schicken --
    ist hier die falsche Wahl. Beim ersten Bootstrap sind es rund 272.000 Zeilen mit
    zwanzig Spalten; das sind 5,4 Millionen Einzelwerte, die einzeln auf Fehlwerte
    geprüft und in Parameter verwandelt werden wollen. Gemessen hing der Import
    dabei minutenlang bei voller CPU-Last, während die Datenbank auf den Client
    wartete.

    COPY schiebt denselben Datenbestand als einen Textstrom hinüber. Weil COPY kein
    ON CONFLICT kennt, geht es über eine temporäre Tabelle: hineinkopieren, dann in
    einem Rutsch mit Upsert übernehmen. Die temporäre Tabelle verschwindet mit der
    Sitzung von selbst.
    """
    if frame.empty:
        return 0

    nutzbar = [c for c in DWD_COLUMNS if c in frame.columns]
    spalten = ["time", *nutzbar]

    roh = session.connection().connection
    with roh.cursor() as cur:
        # Vorher wegräumen statt auf ON COMMIT DROP zu bauen: wird der Import
        # zweimal in derselben Transaktion aufgerufen, steht die Tabelle noch.
        cur.execute(f"DROP TABLE IF EXISTS {TEMP_TABLE}")
        cur.execute(
            f"CREATE TEMP TABLE {TEMP_TABLE} "
            f"(LIKE {DwdHourly.__table__.schema}.dwd_hourly INCLUDING DEFAULTS) "
            "ON COMMIT DROP"
        )
        spaltenliste = ", ".join(f'"{c}"' for c in spalten)
        with cur.copy(
            f"COPY {TEMP_TABLE} ({spaltenliste}) FROM STDIN"
        ) as copy:
            # Vektorisiert statt Zelle für Zelle: erst alles auf object-Spalten mit
            # None für Fehlwerte, dann als Tupel hinüber.
            teil = frame[nutzbar].astype(object).where(frame[nutzbar].notna(), None)
            zeiten = frame.index.to_pydatetime()
            for zeit, werte in zip(zeiten, teil.itertuples(index=False), strict=True):
                copy.write_row((zeit, *werte))

        gesetzt = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in nutzbar)
        cur.execute(
            f"INSERT INTO {DwdHourly.__table__.schema}.dwd_hourly ({spaltenliste}) "
            f"SELECT {spaltenliste} FROM {TEMP_TABLE} "
            f"ON CONFLICT (time) DO UPDATE SET {gesetzt}"
        )

    gesamt = len(frame)
    log.info("DWD: %d Stundenwerte abgelegt", gesamt)
    return gesamt


def load_dwd_hourly(
    session: Session,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> pd.DataFrame:
    """Liest die abgelegten DWD-Stundenwerte auf lückenlosem Stundenraster."""
    stmt = select(DwdHourly.time, *[getattr(DwdHourly, c) for c in DWD_COLUMNS])
    if start is not None:
        stmt = stmt.where(DwdHourly.time >= start)
    if end is not None:
        stmt = stmt.where(DwdHourly.time <= end)

    rows = session.execute(stmt.order_by(DwdHourly.time)).all()
    if not rows:
        return pd.DataFrame()

    frame = pd.DataFrame(rows, columns=["time", *DWD_COLUMNS])
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    frame = frame.set_index("time").sort_index()

    raster = pd.date_range(frame.index.min(), frame.index.max(), freq="h", tz="UTC")
    return frame.reindex(raster).rename_axis("time")


def dwd_coverage(session: Session) -> tuple[datetime, datetime, int] | None:
    """Zeitraum und Zeilenzahl der abgelegten DWD-Daten."""
    zeile = session.execute(
        select(func.min(DwdHourly.time), func.max(DwdHourly.time), func.count())
    ).one()
    if zeile[2] == 0:
        return None
    return zeile[0], zeile[1], int(zeile[2])
