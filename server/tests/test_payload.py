"""Tests des Nachrichtenformats und der Plausibilitätsprüfung.

Leitgedanke aller Tests hier: die Prüfung verwirft *einzelne Werte*, nie ganze
Datensätze. Ein wackelnder Feuchtesensor darf nicht dazu führen, dass auch Druck und
Temperatur derselben Minute verloren gehen -- die sind für die Vorhersage wichtiger.
"""

from datetime import UTC, datetime

import pytest

from wetter.db.sensors import SensorState
from wetter.ingest.payload import PayloadError, parse_batch

JETZT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def nachricht(**kw) -> dict:
    basis = {
        "station": "garten",
        "fw": "0.1.0",
        "mode": "normal",
        "battery_v": 3.92,
        "rssi": -67,
        "sensors": {"bme280": "productive"},
        "records": [
            {
                "t": "2026-09-10T11:58:00Z",
                "seq": 4711,
                "temp": 12.3,
                "hum": 78.1,
                "press": 985.2,
                "wind": 2.4,
                "dir": 225,
                "rain": 0.0,
            }
        ],
    }
    basis.update(kw)
    return basis


def test_normaler_stapel_wird_gelesen():
    b = parse_batch(nachricht(), now=JETZT)
    assert b.station_key == "garten"
    assert b.firmware == "0.1.0"
    assert b.power_mode == "normal"
    assert b.battery_v == pytest.approx(3.92)
    assert b.rssi_dbm == -67
    assert b.sensor_states == {"bme280": SensorState.PRODUCTIVE}
    assert len(b.readings) == 1
    r = b.readings[0]
    assert r.seq == 4711
    assert r.values["temperature_c"] == pytest.approx(12.3)
    assert r.values["pressure_station_hpa"] == pytest.approx(985.2)
    assert not b.rejected


def test_unix_zeitstempel_wird_akzeptiert():
    b = parse_batch(
        nachricht(records=[{"t": 1789041480, "temp": 10.0}]), now=JETZT
    )
    assert len(b.readings) == 1
    assert b.readings[0].time.tzinfo is not None


def test_fehlende_station_ist_ein_fehler():
    with pytest.raises(PayloadError, match="station"):
        parse_batch(nachricht(station=""), now=JETZT)


def test_fehlende_records_sind_ein_fehler():
    with pytest.raises(PayloadError, match="records"):
        parse_batch({"station": "garten"}, now=JETZT)


def test_unplausibler_wert_wird_verworfen_der_rest_bleibt():
    """Die Kernzusicherung der Prüfung."""
    b = parse_batch(
        nachricht(records=[{"t": "2026-09-10T11:58:00Z", "temp": 12.3, "hum": 250.0}]),
        now=JETZT,
    )
    assert len(b.readings) == 1
    assert "humidity_pct" not in b.readings[0].values
    assert b.readings[0].values["temperature_c"] == pytest.approx(12.3)
    assert any("humidity_pct" in m for m in b.rejected)


def test_sprung_wird_verworfen_obwohl_der_wert_moeglich_waere():
    """I2C-Fehler liefern gern Werte, die für sich genommen plausibel sind.

    45 Grad liegt im erlaubten Bereich, ist aber 33 K neben dem Nachbarwert. Der
    Datensatz enthält sonst nichts und fällt deshalb ganz weg -- gespeichert wird
    lieber eine Lücke als ein erfundener Wert.
    """
    b = parse_batch(
        nachricht(
            records=[
                {"t": "2026-09-10T11:56:00Z", "temp": 12.0},
                {"t": "2026-09-10T11:57:00Z", "temp": 12.1},
                {"t": "2026-09-10T11:58:00Z", "temp": 45.0},
                {"t": "2026-09-10T11:59:00Z", "temp": 12.2},
            ]
        ),
        now=JETZT,
    )
    assert [r.values["temperature_c"] for r in b.readings] == [12.0, 12.1, 12.2]
    assert any("springt" in m for m in b.rejected)


def test_sprung_laesst_die_uebrigen_werte_derselben_minute_stehen():
    """Der eigentliche Punkt: ein kaputter Sensor reisst die anderen nicht mit.

    Der Druck derselben Minute ist für die Vorhersage wichtiger als die Temperatur
    und muss den Ausreisser überleben.
    """
    b = parse_batch(
        nachricht(
            records=[
                {"t": "2026-09-10T11:57:00Z", "temp": 12.0, "press": 990.0},
                {"t": "2026-09-10T11:58:00Z", "temp": 45.0, "press": 990.2},
            ]
        ),
        now=JETZT,
    )
    assert len(b.readings) == 2
    zweiter = b.readings[1].values
    assert "temperature_c" not in zweiter
    assert zweiter["pressure_station_hpa"] == pytest.approx(990.2)


def test_langsame_aenderung_ueber_stunden_bleibt_erhalten():
    """Zehn Grad über sechs Stunden sind normal, nicht verdächtig."""
    b = parse_batch(
        nachricht(
            records=[
                {"t": "2026-09-10T06:00:00Z", "temp": 5.0},
                {"t": "2026-09-10T09:00:00Z", "temp": 10.0},
                {"t": "2026-09-10T11:00:00Z", "temp": 15.0},
            ]
        ),
        now=JETZT,
    )
    assert [r.values["temperature_c"] for r in b.readings] == [5.0, 10.0, 15.0]
    assert not b.rejected


def test_zu_alter_zeitstempel_wird_abgelehnt():
    b = parse_batch(nachricht(records=[{"t": 0, "temp": 10.0}]), now=JETZT)
    assert not b.readings
    assert any("zu alt" in m for m in b.rejected)


def test_zeitstempel_aus_der_zukunft_wird_abgelehnt():
    b = parse_batch(
        nachricht(records=[{"t": "2027-01-01T00:00:00Z", "temp": 10.0}]), now=JETZT
    )
    assert not b.readings
    assert any("Zukunft" in m for m in b.rejected)


def test_kleiner_vorlauf_wird_toleriert():
    """Die Uhr der Station darf ein paar Minuten vorgehen."""
    b = parse_batch(
        nachricht(records=[{"t": "2026-09-10T12:05:00Z", "temp": 10.0}]), now=JETZT
    )
    assert len(b.readings) == 1


def test_datensaetze_werden_zeitlich_sortiert():
    b = parse_batch(
        nachricht(
            records=[
                {"t": "2026-09-10T11:59:00Z", "temp": 12.0},
                {"t": "2026-09-10T11:57:00Z", "temp": 11.0},
                {"t": "2026-09-10T11:58:00Z", "temp": 11.5},
            ]
        ),
        now=JETZT,
    )
    assert [r.values["temperature_c"] for r in b.readings] == [11.0, 11.5, 12.0]


def test_leerer_datensatz_faellt_raus():
    b = parse_batch(
        nachricht(
            records=[
                {"t": "2026-09-10T11:58:00Z"},
                {"t": "2026-09-10T11:59:00Z", "temp": 9.0},
            ]
        ),
        now=JETZT,
    )
    assert len(b.readings) == 1


def test_windrichtung_360_wird_zu_null():
    b = parse_batch(
        nachricht(records=[{"t": "2026-09-10T11:58:00Z", "dir": 360}]), now=JETZT
    )
    assert b.readings[0].values["wind_dir_deg"] == pytest.approx(0.0)


def test_unbekannter_sensor_wird_gemeldet_nicht_uebernommen():
    b = parse_batch(nachricht(sensors={"gibtsnicht": "productive"}), now=JETZT)
    assert b.sensor_states == {}
    assert any("gibtsnicht" in m for m in b.rejected)


def test_unbekannter_zustand_wird_gemeldet():
    b = parse_batch(nachricht(sensors={"bme280": "vielleicht"}), now=JETZT)
    assert b.sensor_states == {}
    assert any("vielleicht" in m for m in b.rejected)


def test_testmodus_wird_uebernommen():
    b = parse_batch(
        nachricht(sensors={"bme280": "productive", "mlx90614": "test"}), now=JETZT
    )
    assert b.sensor_states["mlx90614"] is SensorState.TEST


def test_unendliche_und_fehlende_werte_werden_ignoriert():
    b = parse_batch(
        nachricht(
            records=[
                {"t": "2026-09-10T11:58:00Z", "temp": "nan", "hum": None, "press": 990.0}
            ]
        ),
        now=JETZT,
    )
    werte = b.readings[0].values
    assert "temperature_c" not in werte
    assert "humidity_pct" not in werte
    assert werte["pressure_station_hpa"] == pytest.approx(990.0)


def test_kaputter_akkuwert_kippt_nicht_den_stapel():
    b = parse_batch(nachricht(battery_v=99.0), now=JETZT)
    assert b.battery_v is None
    assert len(b.readings) == 1
    assert any("battery_v" in m for m in b.rejected)


def test_text_statt_zahl_wird_verworfen():
    b = parse_batch(
        nachricht(
            records=[{"t": "2026-09-10T11:58:00Z", "temp": "warm", "press": 990.0}]
        ),
        now=JETZT,
    )
    assert "temperature_c" not in b.readings[0].values
    assert b.readings[0].values["pressure_station_hpa"] == pytest.approx(990.0)
