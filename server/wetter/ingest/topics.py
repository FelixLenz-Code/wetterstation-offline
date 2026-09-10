"""MQTT-Themen und Home-Assistant-Discovery.

Aufteilung der Themen:

    wetter/station/<key>/batch    Station -> Server, Messstapel, QoS 1
    wetter/station/<key>/status   Station -> Server, retained, Zustand der Station
    wetter/station/<key>/cmd      Server -> Station, Konfiguration
    homeassistant/.../config      retained, damit HA die Sensoren selbst findet
"""

from __future__ import annotations

import json

from wetter.db.sensors import SENSORS, SensorInfo

BASE = "wetter/station"
HA_PREFIX = "homeassistant"


def batch_topic(station_key: str) -> str:
    return f"{BASE}/{station_key}/batch"


def status_topic(station_key: str) -> str:
    return f"{BASE}/{station_key}/status"


def command_topic(station_key: str) -> str:
    return f"{BASE}/{station_key}/cmd"


def subscription() -> str:
    """Ein Abo für alle Stationen -- ``+`` steht für genau eine Ebene."""
    return f"{BASE}/+/batch"


def station_from_topic(topic: str) -> str | None:
    """Zieht den Stationsschlüssel aus einem Thema."""
    teile = topic.split("/")
    if len(teile) == 4 and teile[0] == "wetter" and teile[1] == "station":
        return teile[2]
    return None


#: Wie Home Assistant die einzelnen Messgrößen darstellen soll.
#: Ohne device_class und unit zeigt HA nur nackte Zahlen ohne Verlauf und ohne
#: Einheit -- mit ihnen bekommt jede Größe automatisch das richtige Symbol, die
#: richtige Achse und die Langzeitstatistik.
HA_FIELDS: dict[str, dict[str, str]] = {
    "temperature_c": {
        "name": "Temperatur",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    },
    "humidity_pct": {
        "name": "Luftfeuchte",
        "device_class": "humidity",
        "unit_of_measurement": "%",
        "state_class": "measurement",
    },
    "pressure_station_hpa": {
        "name": "Luftdruck",
        "device_class": "pressure",
        "unit_of_measurement": "hPa",
        "state_class": "measurement",
    },
    "wind_speed_ms": {
        "name": "Windgeschwindigkeit",
        "device_class": "wind_speed",
        "unit_of_measurement": "m/s",
        "state_class": "measurement",
    },
    "wind_gust_ms": {
        "name": "Windböe",
        "device_class": "wind_speed",
        "unit_of_measurement": "m/s",
        "state_class": "measurement",
    },
    "wind_dir_deg": {
        "name": "Windrichtung",
        "unit_of_measurement": "°",
        "state_class": "measurement",
        "icon": "mdi:compass-outline",
    },
    "precip_mm": {
        "name": "Niederschlag",
        "device_class": "precipitation",
        "unit_of_measurement": "mm",
        # total_increasing statt measurement: HA weiss dann, dass es Tages- und
        # Monatssummen bilden darf und ein Ruecksprung ein Zaehlerueberlauf ist.
        "state_class": "total_increasing",
    },
    "sky_temp_c": {
        "name": "Himmelstemperatur",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    },
    "lightning_distance_km": {
        "name": "Blitzentfernung",
        "device_class": "distance",
        "unit_of_measurement": "km",
        "state_class": "measurement",
    },
    "illuminance_lux": {
        "name": "Helligkeit",
        "device_class": "illuminance",
        "unit_of_measurement": "lx",
        "state_class": "measurement",
    },
    "global_radiation_wm2": {
        "name": "Globalstrahlung",
        "device_class": "irradiance",
        "unit_of_measurement": "W/m²",
        "state_class": "measurement",
    },
    "battery_v": {
        "name": "Akkuspannung",
        "device_class": "voltage",
        "unit_of_measurement": "V",
        "state_class": "measurement",
    },
}


def discovery_messages(
    station_key: str, station_name: str, *, sensors: list[SensorInfo] | None = None
) -> list[tuple[str, str]]:
    """Erzeugt die retained Discovery-Nachrichten für Home Assistant.

    Gibt Paare aus Thema und JSON zurück. Alle Größen hängen an *einem* Gerät, damit
    HA sie zusammen und nicht als zwölf einzelne Geräte anzeigt.

    Die Werte werden aus dem Status-Thema gelesen, nicht aus den Messstapeln: ein
    Stapel enthält viele Zeitpunkte, HA will aber einen aktuellen Wert. Die Station
    schickt deshalb zusätzlich einen schlanken, retained Status.
    """
    aktive = sensors if sensors is not None else list(SENSORS)
    spalten = {c for s in aktive for c in s.columns} | {"battery_v"}

    geraet = {
        "identifiers": [f"wetterstation_{station_key}"],
        "name": station_name,
        "manufacturer": "Eigenbau",
        "model": "ESP32-Wetterstation",
    }
    status = status_topic(station_key)

    out: list[tuple[str, str]] = []
    for spalte, meta in HA_FIELDS.items():
        if spalte not in spalten:
            continue
        eindeutig = f"wetterstation_{station_key}_{spalte}"
        nutzlast = {
            "unique_id": eindeutig,
            "object_id": eindeutig,
            "state_topic": status,
            "value_template": f"{{{{ value_json.{spalte} }}}}",
            "availability_topic": status,
            "availability_template": "{{ 'online' if value_json.online else 'offline' }}",
            "device": geraet,
            **meta,
        }
        out.append(
            (
                f"{HA_PREFIX}/sensor/{eindeutig}/config",
                json.dumps(nutzlast, ensure_ascii=False),
            )
        )
    return out
