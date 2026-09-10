"""Geprüfte Stapel in die Datenbank schreiben.

Zwei Dinge passieren hier, die zusammengehören: die Messwerte landen in
``measurement``, und die von der Station gemeldeten Sensorzustände werden mit der
Historie in ``sensor_state`` abgeglichen.

Der Abgleich ist der heiklere Teil. Die Station meldet in jeder Nachricht ihren
*aktuellen* Zustand; daraus muss eine lückenlose Historie mit Von-Bis-Zeiträumen
werden, damit sich später sagen lässt, ob ein Messwert von vor drei Wochen aus dem
Testbetrieb stammt.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wetter.db.models import Measurement, SensorStateRow, Station
from wetter.db.sensors import SensorState, StateInterval
from wetter.ingest.payload import Batch

log = logging.getLogger(__name__)

#: Spalten von measurement, die aus Messwerten kommen (ohne Schlüssel und Zustand).
VALUE_COLUMNS: tuple[str, ...] = (
    "temperature_c",
    "humidity_pct",
    "pressure_station_hpa",
    "wind_speed_ms",
    "wind_gust_ms",
    "wind_dir_deg",
    "precip_mm",
    "sky_temp_c",
    "lightning_count",
    "lightning_distance_km",
    "illuminance_lux",
    "panel_voltage_v",
    "panel_current_ma",
    "global_radiation_wm2",
)


def get_or_create_station(
    session: Session,
    key: str,
    *,
    name: str | None = None,
    latitude: float = 0.0,
    longitude: float = 0.0,
    altitude_m: float = 0.0,
) -> Station:
    """Holt die Station oder legt sie an.

    Eine unbekannte Station wird angelegt statt abgelehnt: sonst müsste man vor dem
    ersten Einschalten in der Datenbank herumschreiben. Koordinaten und Höhe bleiben
    zunächst null und werden in der PWA nachgetragen -- ohne die Höhe lässt sich der
    Druck nicht reduzieren, deshalb warnt der Merkmalsbau, solange sie fehlt.
    """
    station = session.scalar(select(Station).where(Station.key == key))
    if station is not None:
        return station
    station = Station(
        key=key,
        name=name or key,
        latitude=latitude,
        longitude=longitude,
        altitude_m=altitude_m,
        created_at=datetime.now(UTC),
    )
    session.add(station)
    session.flush()
    log.info("Neue Station angelegt: %s (id=%s)", key, station.id)
    return station


def write_measurements(session: Session, station: Station, batch: Batch) -> int:
    """Schreibt die Messwerte. Vorhandene Zeitpunkte werden ergänzt, nicht ersetzt.

    Doppelte Zeitpunkte sind der Normalfall, nicht die Ausnahme: der Ringpuffer der
    Station gibt einen Datensatz erst nach dem PUBACK frei, ein Verbindungsabbruch
    genau dazwischen führt also dazu, dass er noch einmal kommt. Deshalb ein Upsert
    statt eines Inserts -- und deshalb werden vorhandene Werte nur dort überschrieben,
    wo der neue Datensatz tatsächlich etwas mitbringt.
    """
    if not batch.readings:
        return 0

    zeilen = []
    for r in batch.readings:
        zeile = {
            "station_id": station.id,
            "time": r.time,
            "seq": r.seq,
            "battery_v": batch.battery_v,
            "rssi_dbm": batch.rssi_dbm,
            "power_mode": batch.power_mode,
        }
        zeile.update({c: r.values.get(c) for c in VALUE_COLUMNS})
        zeilen.append(zeile)

    stmt = insert(Measurement).values(zeilen)
    # COALESCE: ein erneut zugestellter Datensatz darf einen bereits gespeicherten
    # Wert nicht mit NULL überschreiben.
    aktualisiere = {
        c: func.coalesce(getattr(stmt.excluded, c), getattr(Measurement, c))
        for c in VALUE_COLUMNS
    }
    aktualisiere["seq"] = stmt.excluded.seq
    aktualisiere["battery_v"] = stmt.excluded.battery_v
    aktualisiere["rssi_dbm"] = stmt.excluded.rssi_dbm
    aktualisiere["power_mode"] = stmt.excluded.power_mode

    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[Measurement.station_id, Measurement.time],
            set_=aktualisiere,
        )
    )
    return len(zeilen)


def sync_sensor_states(
    session: Session,
    station: Station,
    reported: dict[str, SensorState],
    *,
    at: datetime | None = None,
) -> list[str]:
    """Gleicht die gemeldeten Zustände mit der Historie ab.

    Ändert sich ein Zustand, wird der offene Zeitraum geschlossen und ein neuer
    eröffnet. Bleibt er gleich, passiert nichts -- sonst stünden bei einer Nachricht
    alle zwei Minuten Hunderttausende identischer Zeilen im Jahr.

    Gibt die Sensoren zurück, deren Zustand sich geändert hat.
    """
    zeitpunkt = at or datetime.now(UTC)
    offen = session.scalars(
        select(SensorStateRow).where(
            SensorStateRow.station_id == station.id,
            SensorStateRow.valid_to.is_(None),
        )
    ).all()
    nach_sensor = {row.sensor_key: row for row in offen}

    geaendert: list[str] = []
    for key, zustand in reported.items():
        aktuell = nach_sensor.get(key)
        if aktuell is not None and aktuell.state == zustand.value:
            continue
        if aktuell is not None:
            aktuell.valid_to = zeitpunkt
        session.add(
            SensorStateRow(
                station_id=station.id,
                sensor_key=key,
                state=zustand.value,
                valid_from=zeitpunkt,
            )
        )
        geaendert.append(key)
        log.info(
            "Sensor %s: %s -> %s",
            key,
            aktuell.state if aktuell else "unbekannt",
            zustand.value,
        )

    # Sensoren, die die Station nicht mehr meldet, gelten als abgeklemmt.
    for key, row in nach_sensor.items():
        if key not in reported and row.state != SensorState.INACTIVE.value:
            row.valid_to = zeitpunkt
            session.add(
                SensorStateRow(
                    station_id=station.id,
                    sensor_key=key,
                    state=SensorState.INACTIVE.value,
                    valid_from=zeitpunkt,
                )
            )
            geaendert.append(key)

    return geaendert


def load_state_intervals(
    session: Session, station: Station
) -> list[StateInterval]:
    """Liest die Zustandshistorie für die Maskierung im Merkmalsbau."""
    rows = session.scalars(
        select(SensorStateRow)
        .where(SensorStateRow.station_id == station.id)
        .order_by(SensorStateRow.valid_from)
    ).all()
    return [
        StateInterval(
            sensor_key=row.sensor_key,
            state=SensorState(row.state),
            valid_from=pd.Timestamp(row.valid_from),
            valid_to=pd.Timestamp(row.valid_to) if row.valid_to else None,
        )
        for row in rows
    ]


def store_batch(session: Session, batch: Batch) -> tuple[int, list[str]]:
    """Schreibt einen geprüften Stapel komplett weg."""
    station = get_or_create_station(session, batch.station_key)
    geaendert = sync_sensor_states(session, station, batch.sensor_states)
    anzahl = write_measurements(session, station, batch)
    if batch.rejected:
        log.warning(
            "%s: %d Werte verworfen: %s",
            batch.station_key,
            len(batch.rejected),
            "; ".join(batch.rejected[:5]),
        )
    return anzahl, geaendert
