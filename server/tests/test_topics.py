"""Tests der MQTT-Themen und der Home-Assistant-Discovery."""

import json

import pytest

from wetter.db.sensors import BY_KEY
from wetter.ingest.topics import (
    HA_FIELDS,
    batch_topic,
    command_topic,
    discovery_messages,
    station_from_topic,
    status_topic,
    subscription,
)


def test_themen_folgen_dem_schema():
    assert batch_topic("garten") == "wetter/station/garten/batch"
    assert status_topic("garten") == "wetter/station/garten/status"
    assert command_topic("garten") == "wetter/station/garten/cmd"


def test_abo_deckt_alle_stationen_ab():
    assert subscription() == "wetter/station/+/batch"


def test_station_wird_aus_dem_thema_gelesen():
    assert station_from_topic("wetter/station/garten/batch") == "garten"
    assert station_from_topic("wetter/station/dach/status") == "dach"


def test_fremdes_thema_ergibt_keine_station():
    assert station_from_topic("homeassistant/sensor/x/config") is None
    assert station_from_topic("wetter/station/garten") is None
    assert station_from_topic("") is None


def test_discovery_ist_gueltiges_json():
    for thema, nutzlast in discovery_messages("garten", "Wetterstation Garten"):
        assert thema.startswith("homeassistant/sensor/")
        json.loads(nutzlast)


def test_alle_groessen_haengen_an_einem_geraet():
    """Sonst zeigt Home Assistant zwölf einzelne Geräte statt einer Station."""
    geraete = set()
    for _, nutzlast in discovery_messages("garten", "Wetterstation Garten"):
        d = json.loads(nutzlast)
        geraete.add(tuple(d["device"]["identifiers"]))
    assert len(geraete) == 1


def test_eindeutige_kennungen_sind_wirklich_eindeutig():
    kennungen = [
        json.loads(n)["unique_id"] for _, n in discovery_messages("garten", "Garten")
    ]
    assert len(kennungen) == len(set(kennungen))


def test_zwei_stationen_kollidieren_nicht():
    a = {json.loads(n)["unique_id"] for _, n in discovery_messages("garten", "A")}
    b = {json.loads(n)["unique_id"] for _, n in discovery_messages("dach", "B")}
    assert not (a & b)


def test_nur_gemeldete_sensoren_erscheinen():
    """Ein Sensor, der noch nicht gekauft ist, soll in HA nicht auftauchen."""
    nachrichten = discovery_messages(
        "garten", "Garten", sensors=[BY_KEY["bme280"]]
    )
    spalten = {json.loads(n)["value_template"] for _, n in nachrichten}
    assert any("temperature_c" in s for s in spalten)
    assert not any("sky_temp_c" in s for s in spalten)
    assert not any("lightning" in s for s in spalten)


def test_niederschlag_ist_als_summe_ausgezeichnet():
    """total_increasing sagt HA, dass es Tages- und Monatssummen bilden darf."""
    assert HA_FIELDS["precip_mm"]["state_class"] == "total_increasing"
    assert HA_FIELDS["temperature_c"]["state_class"] == "measurement"


@pytest.mark.parametrize(
    ("spalte", "device_class"),
    [
        ("temperature_c", "temperature"),
        ("humidity_pct", "humidity"),
        ("pressure_station_hpa", "pressure"),
        ("precip_mm", "precipitation"),
        ("battery_v", "voltage"),
    ],
)
def test_wichtige_groessen_haben_die_richtige_geraeteklasse(spalte, device_class):
    """Ohne device_class zeigt HA nackte Zahlen ohne Einheit und ohne Verlauf."""
    assert HA_FIELDS[spalte]["device_class"] == device_class
    assert "unit_of_measurement" in HA_FIELDS[spalte]


def test_verfuegbarkeit_haengt_am_status_thema():
    """Fällt die Station aus, sollen die Werte in HA ausgegraut werden."""
    for _, nutzlast in discovery_messages("garten", "Garten"):
        d = json.loads(nutzlast)
        assert d["availability_topic"] == status_topic("garten")
