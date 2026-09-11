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

#: Soviele Zeilen gehen auf einmal in die Datenbank. Bei 600.000 Stunden sind das
#: rund 120 Durchgänge -- gross genug, um schnell zu sein, klein genug, damit die
#: Anweisung nicht das Parameterlimit von Postgres sprengt.
CHUNK = 5000


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

    frame = result.frame
    if frame.empty:
        return 0

    nutzbar = [c for c in DWD_COLUMNS if c in frame.columns]
    gesamt = 0
    for start in range(0, len(frame), CHUNK):
        teil = frame.iloc[start : start + CHUNK]
        zeilen = [
            {
                "time": zeit.to_pydatetime(),
                **{
                    c: (None if pd.isna(reihe[c]) else float(reihe[c]))
                    for c in nutzbar
                },
            }
            for zeit, reihe in teil.iterrows()
        ]
        stmt = insert(DwdHourly).values(zeilen)
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=[DwdHourly.time],
                set_={c: getattr(stmt.excluded, c) for c in nutzbar},
            )
        )
        gesamt += len(zeilen)

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
