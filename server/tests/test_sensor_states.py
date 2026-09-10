"""Tests der Sensorzustände -- vor allem des Testmodus.

Die zentrale Zusicherung: Werte eines Sensors, der im Testmodus läuft, dürfen in der
Anzeige erscheinen, aber niemals im Merkmalsbau oder Training landen. Ohne diesen
Schutz lernt das Modell aus einem BME280, der bei 22 Grad und Windstille auf dem
Schreibtisch liegt.
"""

import numpy as np
import pandas as pd
import pytest

from wetter.db.sensors import (
    BY_KEY,
    COLUMN_OWNER,
    SENSORS,
    SensorState,
    StateInterval,
    current_states,
    mask_by_state,
)


@pytest.fixture
def frame() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=6, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "temperature_c": np.arange(6, dtype=float),
            "pressure_station_hpa": np.full(6, 1000.0),
            "humidity_pct": np.full(6, 60.0),
            "precip_mm": np.zeros(6),
            "sky_temp_c": np.full(6, -30.0),
        },
        index=idx,
    )


def _durchgehend(key: str, state: SensorState) -> StateInterval:
    return StateInterval(key, state, pd.Timestamp("2020-01-01", tz="UTC"), None)


def test_produktive_sensoren_bleiben_erhalten(frame):
    intervalle = [
        _durchgehend("bme280", SensorState.PRODUCTIVE),
        _durchgehend("rain_gauge", SensorState.PRODUCTIVE),
        _durchgehend("mlx90614", SensorState.PRODUCTIVE),
    ]
    out = mask_by_state(frame, intervalle)
    assert out.notna().all().all()


def test_testmodus_wird_aus_dem_training_entfernt(frame):
    """Die Kernzusicherung: Testdaten erreichen das Training nicht."""
    intervalle = [
        _durchgehend("bme280", SensorState.TEST),
        _durchgehend("rain_gauge", SensorState.PRODUCTIVE),
        _durchgehend("mlx90614", SensorState.PRODUCTIVE),
    ]
    out = mask_by_state(frame, intervalle, keep_test=False)
    # Alle drei Spalten des BME280 sind weg.
    assert out["temperature_c"].isna().all()
    assert out["pressure_station_hpa"].isna().all()
    assert out["humidity_pct"].isna().all()
    # Die anderen Sensoren sind davon unberührt.
    assert out["precip_mm"].notna().all()
    assert out["sky_temp_c"].notna().all()


def test_testmodus_bleibt_fuer_die_anzeige_sichtbar(frame):
    """Beim Verkabeln muss man sehen, ob der Sensor überhaupt etwas liefert."""
    intervalle = [_durchgehend("bme280", SensorState.TEST)]
    out = mask_by_state(frame, intervalle, keep_test=True)
    assert out["temperature_c"].notna().all()


def test_zustand_gilt_je_sensor_nicht_je_station(frame):
    """Ein Sensor darf im Test sein, während ein anderer produktiv misst."""
    intervalle = [
        _durchgehend("bme280", SensorState.PRODUCTIVE),
        _durchgehend("mlx90614", SensorState.TEST),
    ]
    out = mask_by_state(frame, intervalle)
    assert out["temperature_c"].notna().all()
    assert out["sky_temp_c"].isna().all()


def test_zustandswechsel_wirkt_ab_dem_zeitpunkt(frame):
    """Der Sensor wandert um 03:00 vom Schreibtisch nach draußen."""
    wechsel = pd.Timestamp("2026-01-01 03:00", tz="UTC")
    intervalle = [
        StateInterval(
            "bme280", SensorState.TEST, pd.Timestamp("2020-01-01", tz="UTC"), wechsel
        ),
        StateInterval("bme280", SensorState.PRODUCTIVE, wechsel, None),
    ]
    out = mask_by_state(frame, intervalle)
    assert out["temperature_c"].iloc[:3].isna().all()
    assert out["temperature_c"].iloc[3:].notna().all()


def test_nachtraeglich_als_test_markierter_zeitraum(frame):
    """Der Modus wurde beim Basteln vergessen und wird hinterher korrigiert."""
    intervalle = [
        StateInterval(
            "bme280",
            SensorState.PRODUCTIVE,
            pd.Timestamp("2020-01-01", tz="UTC"),
            pd.Timestamp("2026-01-01 02:00", tz="UTC"),
        ),
        StateInterval(
            "bme280",
            SensorState.TEST,
            pd.Timestamp("2026-01-01 02:00", tz="UTC"),
            pd.Timestamp("2026-01-01 04:00", tz="UTC"),
        ),
        StateInterval(
            "bme280", SensorState.PRODUCTIVE, pd.Timestamp("2026-01-01 04:00", tz="UTC")
        ),
    ]
    out = mask_by_state(frame, intervalle)
    vorhanden = out["temperature_c"].notna().to_numpy()
    assert list(vorhanden) == [True, True, False, False, True, True]


def test_ohne_angabe_gilt_der_wert_als_unbekannt(frame):
    """Vorsichtige Richtung: lieber eine Lücke als ein falsch geerbter Zustand."""
    out = mask_by_state(frame, [])
    assert out.isna().all().all()


def test_inaktiver_sensor_wird_verworfen(frame):
    intervalle = [_durchgehend("bme280", SensorState.INACTIVE)]
    out = mask_by_state(frame, intervalle)
    assert out["temperature_c"].isna().all()


def test_maskierung_veraendert_die_eingabe_nicht(frame):
    original = frame.copy()
    mask_by_state(frame, [])
    pd.testing.assert_frame_equal(frame, original)


def test_leere_tabelle_bleibt_leer():
    leer = pd.DataFrame(index=pd.DatetimeIndex([], tz="UTC"))
    assert mask_by_state(leer, []).empty


def test_aktueller_zustand_je_sensor():
    intervalle = [
        _durchgehend("bme280", SensorState.PRODUCTIVE),
        _durchgehend("mlx90614", SensorState.TEST),
    ]
    zustaende = current_states(intervalle)
    assert zustaende["bme280"] is SensorState.PRODUCTIVE
    assert zustaende["mlx90614"] is SensorState.TEST
    # Noch nicht gekaufte Sensoren gelten als inaktiv.
    assert zustaende["as3935"] is SensorState.INACTIVE


def test_abgeschlossener_zeitraum_gilt_nicht_mehr_als_aktuell():
    intervalle = [
        StateInterval(
            "bme280",
            SensorState.PRODUCTIVE,
            pd.Timestamp("2020-01-01", tz="UTC"),
            pd.Timestamp("2026-01-01", tz="UTC"),
        )
    ]
    assert current_states(intervalle)["bme280"] is SensorState.INACTIVE


def test_jede_spalte_gehoert_genau_einem_sensor():
    """Sonst wäre unklar, welcher Zustand für eine Spalte gilt."""
    alle = [c for s in SENSORS for c in s.columns]
    assert len(alle) == len(set(alle))
    assert set(COLUMN_OWNER) == set(alle)


def test_bme280_ist_pflicht_die_zusatzsensoren_nicht():
    assert not BY_KEY["bme280"].optional
    assert BY_KEY["as3935"].optional
    assert BY_KEY["mlx90614"].optional
