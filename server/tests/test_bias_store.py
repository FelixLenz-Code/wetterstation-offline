"""Tests der Bias-Ablage.

Entstanden aus einem Fehler, der nichts kaputtmachte, was nach einem Fehler aussah:
die Bias-Korrektur wurde beim Training gelernt und angewendet, danach aber
weggeworfen. Die Vorhersage lief auf unkorrigierten Werten -- das Modell dachte in
DWD-Werten und bekam die Rohwerte der Station.

Gemessen an einem vollen Durchlauf mit 0,8 K Aufstellungsversatz: der mittlere
Temperaturfehler auf sechs Stunden stieg von 1,54 auf 2,32 K, und das
10-bis-90-Prozent-Band deckte statt 80 nur noch 50 Prozent der Fälle ab.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import select

from wetter.db.models import BiasMapping, Station
from wetter.models.bias import fit_mapping
from wetter.models.bias_store import apply_to_frame, load_mappings, save_mappings


@pytest.fixture
def station(session) -> Station:
    s = Station(
        key="b",
        name="B",
        latitude=48.0,
        longitude=8.0,
        altitude_m=200.0,
        created_at=datetime.now(UTC),
    )
    session.add(s)
    session.flush()
    return s


def reihe(werte) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(werte), freq="h", tz="UTC")
    return pd.Series(np.asarray(werte, dtype=float), index=idx)


@pytest.fixture
def abbildungen():
    rng = np.random.default_rng(4)
    t = np.arange(2000, dtype=float)
    wahr = 10.0 + 8.0 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 1.5, 2000)
    return {
        "temperature_c": fit_mapping(
            reihe(wahr + 0.8), reihe(wahr), column="temperature_c"
        )
    }


def test_ablegen_und_laden_ist_verlustfrei(session, station, abbildungen):
    assert save_mappings(session, station, abbildungen) == 1
    session.flush()

    zurueck = load_mappings(session, station)
    assert set(zurueck) == {"temperature_c"}
    x = np.linspace(-5.0, 30.0, 60)
    np.testing.assert_allclose(
        zurueck["temperature_c"].apply(x),
        abbildungen["temperature_c"].apply(x),
    )


def test_kennzahlen_werden_mitgespeichert(session, station, abbildungen):
    save_mappings(session, station, abbildungen)
    session.flush()
    zeile = session.scalar(select(BiasMapping))
    assert zeile.samples == 2000
    # Die eigene Station liest 0,8 K zu warm, die Korrektur zieht entsprechend ab.
    assert zeile.median_shift == pytest.approx(-0.8, abs=0.15)


def test_erneutes_speichern_ersetzt_statt_zu_verdoppeln(session, station, abbildungen):
    save_mappings(session, station, abbildungen)
    session.flush()
    save_mappings(session, station, abbildungen)
    session.flush()
    assert len(session.scalars(select(BiasMapping)).all()) == 1


def test_veraltete_abbildung_wird_entfernt(session, station, abbildungen):
    """Fällt ein Sensor weg, darf seine alte Korrektur nicht weiter wirken."""
    save_mappings(session, station, abbildungen)
    session.flush()
    save_mappings(session, station, {})
    session.flush()
    assert session.scalars(select(BiasMapping)).all() == []


def test_anwendung_korrigiert_die_messreihe(session, station, abbildungen):
    save_mappings(session, station, abbildungen)
    session.flush()

    roh = pd.DataFrame(
        {"temperature_c": [10.8, 15.8, 20.8], "pressure_sea_hpa": [1013.0] * 3},
        index=pd.date_range("2026-06-01", periods=3, freq="h", tz="UTC"),
    )
    korrigiert, spalten = apply_to_frame(session, station, roh)

    assert spalten == ["temperature_c"]
    # Rund 0,8 K niedriger -- das ist genau der eingebaute Aufstellungsversatz.
    np.testing.assert_allclose(
        korrigiert["temperature_c"].to_numpy(), [10.0, 15.0, 20.0], atol=0.4
    )
    # Spalten ohne Abbildung bleiben unangetastet.
    assert korrigiert["pressure_sea_hpa"].tolist() == [1013.0] * 3


def test_ohne_abbildung_bleibt_alles_wie_es_ist(session, station):
    roh = pd.DataFrame(
        {"temperature_c": [10.0, 11.0]},
        index=pd.date_range("2026-06-01", periods=2, freq="h", tz="UTC"),
    )
    korrigiert, spalten = apply_to_frame(session, station, roh)
    assert spalten == []
    pd.testing.assert_frame_equal(korrigiert, roh)


def test_abbildungen_sind_stationsbezogen(session, station, abbildungen):
    zweite = Station(
        key="c",
        name="C",
        latitude=49.0,
        longitude=9.0,
        altitude_m=100.0,
        created_at=datetime.now(UTC),
    )
    session.add(zweite)
    session.flush()

    save_mappings(session, station, abbildungen)
    session.flush()
    assert load_mappings(session, zweite) == {}
    assert set(load_mappings(session, station)) == {"temperature_c"}
