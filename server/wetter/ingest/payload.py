"""Nachrichtenformat der Station und Plausibilitätsprüfung.

Die Station schickt Stapel statt Einzelwerte: WLAN einzuschalten kostet weit mehr
Energie als ein paar Kilobyte mehr zu übertragen, und der Ringpuffer muss ohnehin
mehrere Datensätze auf einmal loswerden, wenn er nach einem Ausfall aufholt.

Die Prüfung verwirft *einzelne Werte*, nie ganze Datensätze. Ein wackelnder
Feuchtesensor darf nicht dazu führen, dass auch Druck und Temperatur derselben
Minute verloren gehen -- die sind für die Vorhersage wichtiger.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime

from wetter.db.sensors import BY_KEY, SensorState

#: Erlaubte Wertebereiche je Spalte. Grosszügig gewählt: hier soll grober Unfug
#: auffallen (Sensor abgezogen, I2C-Fehler, Vorzeichenfehler), nicht ungewöhnliches
#: Wetter. Der deutsche Rekord liegt bei 41,2 Grad -- die Grenze bei 60 lässt also
#: reichlich Luft und fängt trotzdem einen kaputten Sensor ab.
RANGES: dict[str, tuple[float, float]] = {
    "temperature_c": (-50.0, 60.0),
    "humidity_pct": (0.0, 100.0),
    "pressure_station_hpa": (500.0, 1100.0),
    "wind_speed_ms": (0.0, 100.0),
    "wind_gust_ms": (0.0, 120.0),
    "wind_dir_deg": (0.0, 360.0),
    "precip_mm": (0.0, 50.0),
    "sky_temp_c": (-80.0, 80.0),
    "lightning_count": (0.0, 1000.0),
    "lightning_distance_km": (0.0, 63.0),
    "illuminance_lux": (0.0, 200000.0),
    "panel_voltage_v": (0.0, 30.0),
    "panel_current_ma": (-100.0, 5000.0),
    "global_radiation_wm2": (0.0, 1400.0),
    "battery_v": (0.0, 20.0),
}

#: Grösste plausible Änderung je Minute. Fängt Ausreisser ab, die im Wertebereich
#: liegen: ein I2C-Fehler liefert gern einen Wert, der für sich genommen möglich ist,
#: aber 30 K neben dem Nachbarwert liegt.
MAX_RATE_PER_MIN: dict[str, float] = {
    "temperature_c": 5.0,
    "humidity_pct": 40.0,
    "pressure_station_hpa": 3.0,
    "sky_temp_c": 30.0,
}

#: Kürzel im Nachrichtenformat -> Spaltenname. Kurze Namen, weil die Station sie
#: über eine energetisch teure Funkstrecke schickt.
FIELD_MAP: dict[str, str] = {
    "temp": "temperature_c",
    "hum": "humidity_pct",
    "press": "pressure_station_hpa",
    "wind": "wind_speed_ms",
    "gust": "wind_gust_ms",
    "dir": "wind_dir_deg",
    "rain": "precip_mm",
    "sky": "sky_temp_c",
    "lcnt": "lightning_count",
    "ldist": "lightning_distance_km",
    "lux": "illuminance_lux",
    "pv": "panel_voltage_v",
    "pi": "panel_current_ma",
    "rad": "global_radiation_wm2",
}

#: Ältere und neuere Zeitstempel als das werden abgelehnt. Die Station kann nach
#: einem Neustart ohne Zeitabgleich Unsinn schicken; ein Datensatz von 1970 oder
#: aus dem Jahr 2100 würde die Merkmalsberechnung zerreissen.
MIN_TIME = datetime(2024, 1, 1, tzinfo=UTC)
MAX_FUTURE_SECONDS = 3600.0


@dataclass
class Reading:
    """Ein geprüfter Messzeitpunkt."""

    time: datetime
    seq: int | None = None
    values: dict[str, float] = field(default_factory=dict)


@dataclass
class Batch:
    """Ein Stapel Messungen samt Stationszustand."""

    station_key: str
    readings: list[Reading] = field(default_factory=list)
    sensor_states: dict[str, SensorState] = field(default_factory=dict)
    firmware: str | None = None
    power_mode: str | None = None
    battery_v: float | None = None
    rssi_dbm: int | None = None
    rejected: list[str] = field(default_factory=list)
    """Verworfene Werte mit Begründung -- landet im Log, nicht in der Datenbank."""


class PayloadError(ValueError):
    """Die Nachricht war so kaputt, dass sich nichts retten liess."""


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _parse_time(raw: object) -> datetime | None:
    """Akzeptiert Unix-Sekunden oder ISO-8601."""
    if isinstance(raw, int | float) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return None


def check_ranges(values: dict[str, float], rejected: list[str]) -> dict[str, float]:
    """Verwirft Werte ausserhalb ihres physikalisch möglichen Bereichs."""
    out: dict[str, float] = {}
    for name, value in values.items():
        grenzen = RANGES.get(name)
        if grenzen is None:
            out[name] = value
            continue
        low, high = grenzen
        if low <= value <= high:
            out[name] = value
        else:
            rejected.append(f"{name}={value:g} ausserhalb [{low:g}, {high:g}]")
    return out


def check_rates(readings: list[Reading], rejected: list[str]) -> None:
    """Verwirft Sprünge, die für sich genommen plausibel wären.

    Arbeitet auf dem Stapel in zeitlicher Reihenfolge. Verworfen wird immer der
    *spätere* Wert -- welcher der beiden falsch ist, lässt sich nicht entscheiden,
    aber so bleibt der bereits bestätigte Verlauf stehen.
    """
    for name, max_rate in MAX_RATE_PER_MIN.items():
        vorher: tuple[datetime, float] | None = None
        for r in readings:
            wert = r.values.get(name)
            if wert is None:
                continue
            if vorher is not None:
                minuten = (r.time - vorher[0]).total_seconds() / 60.0
                if 0 < minuten <= 60:
                    aenderung = abs(wert - vorher[1]) / minuten
                    if aenderung > max_rate:
                        rejected.append(
                            f"{name}={wert:g} springt {aenderung:.1f}/min "
                            f"(erlaubt {max_rate:g})"
                        )
                        del r.values[name]
                        continue
            vorher = (r.time, wert)


def parse_batch(data: dict, *, now: datetime | None = None) -> Batch:
    """Liest eine Stapelnachricht und prüft sie.

    Wirft nur, wenn die Nachricht als Ganzes unbrauchbar ist. Einzelne unplausible
    Werte werden verworfen und in ``rejected`` vermerkt.
    """
    if not isinstance(data, dict):
        raise PayloadError("Nachricht ist kein Objekt")

    station = data.get("station")
    if not isinstance(station, str) or not station:
        raise PayloadError("station fehlt oder ist leer")

    jetzt = now or datetime.now(UTC)
    batch = Batch(station_key=station)

    fw = data.get("fw")
    batch.firmware = fw if isinstance(fw, str) else None
    mode = data.get("mode")
    batch.power_mode = mode if isinstance(mode, str) else None

    akku = _as_float(data.get("battery_v"))
    if akku is not None:
        low, high = RANGES["battery_v"]
        if low <= akku <= high:
            batch.battery_v = akku
        else:
            batch.rejected.append(f"battery_v={akku:g} unplausibel")

    rssi = _as_float(data.get("rssi"))
    if rssi is not None and -120 <= rssi <= 0:
        batch.rssi_dbm = int(rssi)

    zustaende = data.get("sensors")
    if isinstance(zustaende, dict):
        for key, roh in zustaende.items():
            if key not in BY_KEY:
                batch.rejected.append(f"unbekannter Sensor {key!r}")
                continue
            try:
                batch.sensor_states[key] = SensorState(roh)
            except ValueError:
                batch.rejected.append(f"unbekannter Zustand {roh!r} fuer {key}")

    records = data.get("records")
    if not isinstance(records, list):
        raise PayloadError("records fehlt oder ist keine Liste")

    for eintrag in records:
        if not isinstance(eintrag, dict):
            batch.rejected.append("Datensatz ist kein Objekt")
            continue
        zeit = _parse_time(eintrag.get("t"))
        if zeit is None:
            batch.rejected.append("Zeitstempel fehlt oder unlesbar")
            continue
        if zeit < MIN_TIME:
            batch.rejected.append(f"Zeitstempel {zeit.isoformat()} zu alt")
            continue
        if (zeit - jetzt).total_seconds() > MAX_FUTURE_SECONDS:
            batch.rejected.append(f"Zeitstempel {zeit.isoformat()} liegt in der Zukunft")
            continue

        werte: dict[str, float] = {}
        for kurz, spalte in FIELD_MAP.items():
            f = _as_float(eintrag.get(kurz))
            if f is not None:
                werte[spalte] = f
        # 360 Grad und 0 Grad sind dieselbe Richtung -- normalisieren, damit der
        # Bereichstest nicht bei exakt 360 zuschlägt.
        if "wind_dir_deg" in werte:
            werte["wind_dir_deg"] %= 360.0

        roh_seq = eintrag.get("seq")
        seq = (
            int(roh_seq)
            if isinstance(roh_seq, int) and not isinstance(roh_seq, bool)
            else None
        )
        batch.readings.append(
            Reading(
                time=zeit,
                seq=seq,
                values=check_ranges(werte, batch.rejected),
            )
        )

    batch.readings.sort(key=lambda r: r.time)
    check_rates(batch.readings, batch.rejected)
    # Datensätze, von denen nach der Prüfung nichts übrig ist, fallen raus.
    batch.readings = [r for r in batch.readings if r.values]
    return batch
