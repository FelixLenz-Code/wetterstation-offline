"""Tests der DWD-Ablage.

Entstanden aus einem Fehler, den kein Test mit einer kurzen Kunstreihe gezeigt hat:
die Blockgrösse war fest auf 5000 Zeilen gesetzt. Bei fünf Spalten ist das
unauffällig, bei den zwanzig Spalten der echten DWD-Daten sprengt es das
Parameterlimit von Postgres. Aufgefallen ist es erst beim ersten Bootstrap in voller
Grösse -- also genau da, wo es beim Nutzer aufgefallen wäre.

Deshalb prüfen die Tests hier ausdrücklich mit der *vollen* Spaltenbreite und mit
mehr Zeilen, als in einen Block passen.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import func, select

from wetter.db.models import DwdHourly, DwdStation
from wetter.dwd.bootstrap import BootstrapResult
from wetter.dwd.stations import Candidate
from wetter.dwd.stations import Station as DwdStationInfo
from wetter.dwd.store import (
    DWD_COLUMNS,
    MAX_PARAMS,
    chunk_size,
    dwd_coverage,
    load_dwd_hourly,
    save_bootstrap,
)


def test_blockgroesse_bleibt_unter_dem_parameterlimit():
    """Die Zusicherung, an der es gescheitert ist."""
    for spalten in (1, 5, 20, 50, 200):
        assert chunk_size(spalten) * spalten <= MAX_PARAMS


def test_blockgroesse_passt_zur_echten_spaltenzahl():
    spalten = len(DWD_COLUMNS) + 1
    assert chunk_size(spalten) * spalten <= MAX_PARAMS
    # Und nicht absurd klein -- sonst dauert der Import ewig.
    assert chunk_size(spalten) > 1000


def test_blockgroesse_bei_unsinniger_eingabe():
    assert chunk_size(0) == 1
    assert chunk_size(-5) == 1


def _voller_rahmen(stunden: int) -> pd.DataFrame:
    """Eine Reihe mit *allen* DWD-Spalten -- genau darum geht es hier."""
    idx = pd.date_range("2020-01-01", periods=stunden, freq="h", tz="UTC")
    rng = np.random.default_rng(11)
    return pd.DataFrame(
        {c: rng.normal(10, 3, stunden) for c in DWD_COLUMNS},
        index=idx,
    )


def _ergebnis(frame: pd.DataFrame) -> BootstrapResult:
    station = DwdStationInfo(
        station_id=1443,
        start=datetime(1951, 1, 1).date(),
        end=datetime(2026, 9, 9).date(),
        altitude_m=237.0,
        latitude=47.9959,
        longitude=7.8522,
        name="Freiburg",
        state="Baden-Württemberg",
    )
    return BootstrapResult(
        frame=frame,
        stations={"TU": Candidate(station=station, distance_km=2.8)},
        failed={},
    )


def test_voller_bootstrap_ueber_mehrere_bloecke(session):
    """Mehr Zeilen als in einen Block passen, mit voller Spaltenbreite."""
    spalten = len(DWD_COLUMNS) + 1
    stunden = chunk_size(spalten) * 2 + 137  # bewusst kein glattes Vielfaches
    frame = _voller_rahmen(stunden)

    n = save_bootstrap(session, _ergebnis(frame))
    session.flush()

    assert n == stunden
    assert session.scalar(select(func.count()).select_from(DwdHourly)) == stunden


def test_stationsauswahl_wird_mitgeschrieben(session):
    save_bootstrap(session, _ergebnis(_voller_rahmen(100)))
    session.flush()
    zeile = session.scalar(select(DwdStation))
    assert zeile.dataset_key == "TU"
    assert zeile.station_id == 1443
    assert zeile.name == "Freiburg"
    assert zeile.distance_km == pytest.approx(2.8)


def test_erneuter_import_ersetzt_statt_zu_verdoppeln(session):
    frame = _voller_rahmen(500)
    save_bootstrap(session, _ergebnis(frame))
    session.flush()
    # Beim zweiten Lauf sind die Werte anders -- etwa weil der DWD nachgeprüft hat.
    frame2 = frame + 1.0
    save_bootstrap(session, _ergebnis(frame2))
    session.flush()

    assert session.scalar(select(func.count()).select_from(DwdHourly)) == 500
    assert session.scalar(select(func.count()).select_from(DwdStation)) == 1
    gelesen = load_dwd_hourly(session)
    assert gelesen["temperature_c"].iloc[0] == pytest.approx(
        float(frame2["temperature_c"].iloc[0])
    )


def test_lesen_liefert_lueckenloses_stundenraster(session):
    frame = _voller_rahmen(200)
    # Zwei Stunden fehlen im Import -- beim Lesen müssen sie als Lücke auftauchen.
    frame = frame.drop(frame.index[100:102])
    save_bootstrap(session, _ergebnis(frame))
    session.flush()

    gelesen = load_dwd_hourly(session)
    assert len(gelesen) == 200
    assert gelesen["temperature_c"].isna().sum() == 2
    schritte = np.diff(gelesen.index.to_numpy()).astype("timedelta64[m]")
    assert np.all(schritte == np.timedelta64(60, "m"))


def test_zeitraum_wird_gemeldet(session):
    frame = _voller_rahmen(300)
    save_bootstrap(session, _ergebnis(frame))
    session.flush()
    abdeckung = dwd_coverage(session)
    assert abdeckung is not None
    start, ende, anzahl = abdeckung
    assert anzahl == 300
    assert start.replace(tzinfo=UTC) == frame.index.min().to_pydatetime()
    assert ende.replace(tzinfo=UTC) == frame.index.max().to_pydatetime()


def test_leere_datenbank_meldet_keine_abdeckung(session):
    assert dwd_coverage(session) is None


def test_leerer_bootstrap_schreibt_keine_stunden(session):
    n = save_bootstrap(session, _ergebnis(pd.DataFrame()))
    session.flush()
    assert n == 0
    assert session.scalar(select(func.count()).select_from(DwdHourly)) == 0
