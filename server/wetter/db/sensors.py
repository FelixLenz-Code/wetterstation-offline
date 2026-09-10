"""Sensor-Katalog und die Auswertung der Sensorzustände.

Der Kern der Anforderung "Sensoren einzeln abschaltbar": jeder Sensor hat nicht nur
an/aus, sondern drei Zustände. Der Testmodus ist der wichtigste davon -- solange ein
Sensor zum Entwickeln drinnen liegt, dürfen seine Werte angezeigt, aber nicht
trainiert werden. Ein BME280 auf dem Schreibtisch misst 22 Grad und 1013 hPa bei
Windstille; ginge das ins Training, lernte das Modell Unsinn.

Der Zustand hängt am *einzelnen* Sensor, nicht an der Station: der BME280 kann längst
draußen produktiv messen, während der AS3935 noch auf dem Tisch getestet wird.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import pandas as pd


class SensorState(enum.StrEnum):
    """Betriebszustand eines einzelnen Sensors."""

    PRODUCTIVE = "productive"
    """Sensor hängt am endgültigen Platz. Werte fließen ins Training."""

    TEST = "test"
    """Sensor wird gerade verkabelt oder liegt drinnen. Werte werden gespeichert
    und angezeigt, aber von Merkmalsbau, Training und Verifikation ausgeschlossen."""

    INACTIVE = "inactive"
    """Sensor nicht vorhanden oder abgeschaltet. Merkmal gilt als unbekannt."""


@dataclass(frozen=True)
class SensorInfo:
    """Ein Sensor und die kanonischen Spalten, die er liefert."""

    key: str
    label: str
    columns: tuple[str, ...]
    i2c_address: int | None = None
    optional: bool = True


#: Alle Sensoren der Station. ``columns`` nennt die kanonischen Spalten, die dieser
#: Sensor speist -- daran hängt die Maskierung: ist der Sensor nicht produktiv,
#: werden genau diese Spalten auf NaN gesetzt.
SENSORS: tuple[SensorInfo, ...] = (
    SensorInfo(
        key="bme280",
        label="BME280 (Druck, Temperatur, Feuchte)",
        columns=("pressure_station_hpa", "temperature_c", "humidity_pct"),
        i2c_address=0x76,
        optional=False,
    ),
    SensorInfo(
        key="wind_speed",
        label="Anemometer WH-SP-WS01",
        columns=("wind_speed_ms", "wind_gust_ms"),
    ),
    SensorInfo(
        key="wind_vane",
        label="Windfahne",
        columns=("wind_dir_deg",),
    ),
    SensorInfo(
        key="rain_gauge",
        label="Regen-Kippwaage",
        columns=("precip_mm",),
    ),
    SensorInfo(
        key="mlx90614",
        label="MLX90614 (IR-Himmelstemperatur)",
        columns=("sky_temp_c",),
        i2c_address=0x5A,
    ),
    SensorInfo(
        key="as3935",
        label="AS3935 (Blitzerkennung)",
        columns=("lightning_count", "lightning_distance_km"),
        i2c_address=0x03,
    ),
    SensorInfo(
        key="bh1750",
        label="BH1750 (Helligkeit)",
        columns=("illuminance_lux",),
        i2c_address=0x23,
    ),
    SensorInfo(
        key="ina219",
        label="INA219 (Solarpanel, Einstrahlungs-Proxy)",
        columns=("panel_voltage_v", "panel_current_ma", "global_radiation_wm2"),
        i2c_address=0x40,
    ),
)

BY_KEY: dict[str, SensorInfo] = {s.key: s for s in SENSORS}

#: Spalte -> Sensor, der sie liefert.
COLUMN_OWNER: dict[str, str] = {
    col: s.key for s in SENSORS for col in s.columns
}


@dataclass(frozen=True)
class StateInterval:
    """Ein Zeitraum, in dem ein Sensor einen bestimmten Zustand hatte.

    ``valid_to`` ist ``None``, solange der Zustand noch gilt.
    """

    sensor_key: str
    state: SensorState
    valid_from: pd.Timestamp
    valid_to: pd.Timestamp | None = None

    def covers(self, index: pd.DatetimeIndex) -> pd.Series:
        """Maske: welche Zeitpunkte fallen in diesen Zeitraum?"""
        maske = index >= self.valid_from
        if self.valid_to is not None:
            maske &= index < self.valid_to
        return pd.Series(maske, index=index)


def mask_by_state(
    frame: pd.DataFrame,
    intervals: list[StateInterval],
    *,
    keep_test: bool = False,
) -> pd.DataFrame:
    """Setzt Spalten auf NaN, wo ihr Sensor nicht produktiv war.

    Das ist die Stelle, an der der Testmodus wirkt. ``keep_test=True`` behält
    Testdaten -- das nutzt die Anzeige, damit man beim Verkabeln sieht, ob der Sensor
    überhaupt etwas liefert. Merkmalsbau, Training und Verifikation rufen immer mit
    ``keep_test=False`` auf.

    Zeiträume ohne jede Angabe gelten als *unbekannt* und werden ebenfalls verworfen.
    Das ist die vorsichtige Richtung: lieber eine Lücke im Training als ein Sensor,
    der versehentlich als produktiv durchgeht.
    """
    if frame.empty:
        return frame.copy()

    erlaubt = {SensorState.PRODUCTIVE}
    if keep_test:
        erlaubt.add(SensorState.TEST)

    out = frame.copy()
    idx = frame.index

    for sensor in SENSORS:
        spalten = [c for c in sensor.columns if c in out.columns]
        if not spalten:
            continue

        gueltig = pd.Series(False, index=idx)
        for iv in intervals:
            if iv.sensor_key != sensor.key or iv.state not in erlaubt:
                continue
            gueltig |= iv.covers(idx)

        out.loc[~gueltig.to_numpy(), spalten] = float("nan")

    return out


def current_states(intervals: list[StateInterval]) -> dict[str, SensorState]:
    """Zustand je Sensor zum jetzigen Zeitpunkt.

    Sensoren ohne offenen Zeitraum gelten als inaktiv -- der Normalfall für
    Sensoren, die noch gar nicht gekauft sind.
    """
    out: dict[str, SensorState] = {s.key: SensorState.INACTIVE for s in SENSORS}
    for iv in intervals:
        if iv.valid_to is None and iv.sensor_key in out:
            out[iv.sensor_key] = iv.state
    return out
