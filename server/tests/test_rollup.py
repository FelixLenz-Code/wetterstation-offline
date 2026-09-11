"""Tests der Stundenaggregation.

Schwerpunkt liegt auf zwei Stellen, an denen ein naiver Ansatz still falsche Werte
liefert: dem Vektormittel der Windrichtung und der Reihenfolge von Maskierung und
Aggregation.
"""

import numpy as np
import pandas as pd
import pytest

from wetter.db.sensors import SensorState, StateInterval
from wetter.worker.rollup import (
    MIN_SAMPLES,
    aggregate_hourly,
    circular_mean_direction,
)


def _dir(werte, speed=None) -> float:
    return circular_mean_direction(
        pd.Series(werte, dtype=float),
        pd.Series(speed, dtype=float) if speed is not None else None,
    )


# --- Vektormittel der Windrichtung ------------------------------------------


def test_mittel_ueber_den_nordpunkt():
    """Der eigentliche Grund für das Vektormittel.

    Der arithmetische Mittelwert von 350 und 10 Grad ist 180 Grad -- die
    Gegenrichtung. Bei Nordwind meldete die Station dann Südwind.
    """
    assert _dir([350.0, 10.0]) == pytest.approx(0.0, abs=0.01)
    # Zum Vergleich: so falsch wäre es arithmetisch.
    assert np.mean([350.0, 10.0]) == pytest.approx(180.0)


def test_mittel_gleicher_richtungen_bleibt_gleich():
    for grad in (0.0, 45.0, 137.0, 225.0, 359.0):
        assert _dir([grad, grad, grad]) == pytest.approx(grad, abs=0.01)


def test_mittel_zweier_nachbarrichtungen():
    assert _dir([80.0, 100.0]) == pytest.approx(90.0, abs=0.01)
    assert _dir([170.0, 190.0]) == pytest.approx(180.0, abs=0.01)


def test_gegenrichtungen_heben_sich_auf():
    """Bei Wind aus genau entgegengesetzten Richtungen gibt es keine mittlere.

    Eine erfundene Zahl wäre hier schlimmer als keine -- sie sähe wie eine Messung
    aus und ginge als Merkmal ins Modell.
    """
    assert np.isnan(_dir([0.0, 180.0]))
    assert np.isnan(_dir([90.0, 270.0]))


def test_gewichtung_nach_windgeschwindigkeit():
    """Die Richtung bei Windstille ist bedeutungslos und darf nicht mitzählen."""
    # Kräftiger Ostwind, dazu ein Hauch aus Süd -- das Ergebnis muss östlich bleiben.
    assert _dir([90.0, 180.0], speed=[10.0, 0.01]) == pytest.approx(90.0, abs=1.0)
    # Ohne Gewichtung läge es genau dazwischen.
    assert _dir([90.0, 180.0]) == pytest.approx(135.0, abs=0.01)


def test_ohne_gueltige_werte_gibt_es_keine_richtung():
    assert np.isnan(_dir([np.nan, np.nan]))
    assert np.isnan(circular_mean_direction(pd.Series([], dtype=float)))


def test_ergebnis_liegt_immer_im_kreis():
    rng = np.random.default_rng(0)
    for _ in range(50):
        werte = rng.uniform(0, 360, 5)
        d = _dir(list(werte))
        if not np.isnan(d):
            assert 0.0 <= d < 360.0


# --- Aggregation ------------------------------------------------------------


@pytest.fixture
def rohdaten() -> pd.DataFrame:
    """Zwei volle Stunden im Zehn-Minuten-Takt."""
    idx = pd.date_range("2026-09-10 10:00", periods=12, freq="10min", tz="UTC")
    return pd.DataFrame(
        {
            "temperature_c": np.concatenate([np.full(6, 10.0), np.full(6, 12.0)]),
            "humidity_pct": np.full(12, 80.0),
            "pressure_station_hpa": np.full(12, 985.0),
            "wind_speed_ms": np.full(12, 3.0),
            "wind_gust_ms": np.concatenate([np.arange(6.0, 12.0), np.full(6, 5.0)]),
            "wind_dir_deg": np.full(12, 270.0),
            "precip_mm": np.full(12, 0.2),
        },
        index=idx,
    )


def test_temperatur_wird_gemittelt(rohdaten):
    out = aggregate_hourly(rohdaten, altitude_m=237.0)
    assert list(out["temperature_c"]) == [10.0, 12.0]


def test_niederschlag_wird_summiert(rohdaten):
    """Ein Mittelwert wäre hier grob falsch -- Regen summiert sich."""
    out = aggregate_hourly(rohdaten, altitude_m=237.0)
    assert out["precip_mm"].iloc[0] == pytest.approx(1.2)


def test_boe_ist_das_maximum(rohdaten):
    out = aggregate_hourly(rohdaten, altitude_m=237.0)
    assert out["wind_gust_ms"].iloc[0] == pytest.approx(11.0)
    assert out["wind_speed_ms"].iloc[0] == pytest.approx(3.0)


def test_stichprobenzahl_wird_mitgefuehrt(rohdaten):
    """Eine Stunde aus zwei Werten ist etwas anderes als eine aus 360."""
    out = aggregate_hourly(rohdaten, altitude_m=237.0)
    assert list(out["sample_count"]) == [6, 6]


def test_zu_duenne_stunde_wird_verworfen_die_zahl_bleibt():
    idx = pd.DatetimeIndex(["2026-09-10 10:05"], tz="UTC")
    frame = pd.DataFrame({"temperature_c": [10.0], "precip_mm": [0.2]}, index=idx)
    out = aggregate_hourly(frame, altitude_m=237.0, min_samples=MIN_SAMPLES)
    assert np.isnan(out["temperature_c"].iloc[0])
    # Die Stichprobenzahl bleibt sichtbar, damit die Lücke erklärbar ist.
    assert out["sample_count"].iloc[0] == 1


def test_abgeleitete_groessen_werden_ergaenzt(rohdaten):
    """Das Ergebnis muss im selben Schema liegen wie die DWD-Daten."""
    out = aggregate_hourly(rohdaten, altitude_m=237.0)
    assert "dewpoint_c" in out.columns
    assert "pressure_sea_hpa" in out.columns
    # 10 Grad bei 80 % ergeben rund 6,7 Grad Taupunkt.
    assert out["dewpoint_c"].iloc[0] == pytest.approx(6.7, abs=0.2)
    # Auf 237 m liegt der Meeresniveaudruck rund 28 hPa höher.
    assert out["pressure_sea_hpa"].iloc[0] == pytest.approx(1013.0, abs=2.0)


def test_leere_eingabe_ergibt_leere_ausgabe():
    leer = pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))
    assert aggregate_hourly(leer, altitude_m=0.0).empty


def test_teilstunden_werden_der_richtigen_stunde_zugeordnet():
    idx = pd.DatetimeIndex(
        ["2026-09-10 10:59", "2026-09-10 11:00", "2026-09-10 11:59"], tz="UTC"
    )
    frame = pd.DataFrame({"temperature_c": [1.0, 2.0, 4.0]}, index=idx)
    out = aggregate_hourly(frame, altitude_m=0.0, min_samples=1)
    assert list(out.index.hour) == [10, 11]
    assert out["temperature_c"].iloc[1] == pytest.approx(3.0)


# --- Zusammenspiel mit dem Testmodus ----------------------------------------


def test_testdaten_sickern_nicht_ueber_den_mittelwert_ins_training(rohdaten):
    """Die Reihenfolge ist entscheidend: erst maskieren, dann aggregieren.

    Würde man erst mitteln und dann maskieren, wäre der Stundenwert bereits mit
    Testdaten verunreinigt und die Verunreinigung nicht mehr herausrechenbar.
    """
    from wetter.db.sensors import mask_by_state

    intervalle = [
        StateInterval(
            "bme280", SensorState.TEST, pd.Timestamp("2020-01-01", tz="UTC"), None
        ),
        StateInterval(
            "rain_gauge",
            SensorState.PRODUCTIVE,
            pd.Timestamp("2020-01-01", tz="UTC"),
            None,
        ),
    ]
    maskiert = mask_by_state(rohdaten, intervalle, keep_test=False)
    out = aggregate_hourly(maskiert, altitude_m=237.0)

    assert out["temperature_c"].isna().all()
    assert out["pressure_station_hpa"].isna().all()
    # Ohne Temperatur lässt sich auch der Taupunkt nicht bilden.
    assert out["dewpoint_c"].isna().all()
    # Der produktive Regenmesser ist davon unberührt.
    assert out["precip_mm"].iloc[0] == pytest.approx(1.2)
