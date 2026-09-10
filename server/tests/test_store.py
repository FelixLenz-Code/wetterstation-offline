"""Integrationstests des Ingest gegen eine echte Postgres-Datenbank."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from wetter.db.models import Measurement, SensorStateRow, Station
from wetter.db.sensors import SensorState
from wetter.ingest.payload import parse_batch
from wetter.ingest.store import (
    load_state_intervals,
    store_batch,
    sync_sensor_states,
)

JETZT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def nachricht(records, sensors=None, **kw) -> dict:
    basis = {
        "station": "garten",
        "fw": "0.1.0",
        "mode": "normal",
        "battery_v": 3.9,
        "sensors": sensors if sensors is not None else {"bme280": "productive"},
        "records": records,
    }
    basis.update(kw)
    return basis


def test_stapel_wird_geschrieben(session):
    batch = parse_batch(
        nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.3, "press": 985.2}]),
        now=JETZT,
    )
    anzahl, _ = store_batch(session, batch)
    session.commit()

    assert anzahl == 1
    zeile = session.scalar(select(Measurement))
    assert zeile.temperature_c == pytest.approx(12.3)
    assert zeile.pressure_station_hpa == pytest.approx(985.2)
    assert zeile.battery_v == pytest.approx(3.9)
    assert zeile.power_mode == "normal"


def test_unbekannte_station_wird_angelegt(session):
    batch = parse_batch(
        nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.0}]), now=JETZT
    )
    store_batch(session, batch)
    session.commit()
    station = session.scalar(select(Station))
    assert station.key == "garten"


def test_erneute_zustellung_erzeugt_keine_zweite_zeile(session):
    """Der Ringpuffer gibt einen Datensatz erst nach dem PUBACK frei.

    Bricht die Verbindung genau dazwischen ab, kommt derselbe Datensatz noch einmal.
    Das ist der Normalfall, nicht die Ausnahme -- und darf keine Dublette erzeugen.
    """
    daten = nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.3}])
    store_batch(session, parse_batch(daten, now=JETZT))
    session.commit()
    store_batch(session, parse_batch(daten, now=JETZT))
    session.commit()

    assert session.scalar(select(func.count()).select_from(Measurement)) == 1


def test_erneute_zustellung_ueberschreibt_keinen_wert_mit_null(session):
    """Kommt ein Datensatz nachträglich ohne Druck, bleibt der gespeicherte stehen."""
    store_batch(
        session,
        parse_batch(
            nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.3, "press": 985.2}]),
            now=JETZT,
        ),
    )
    session.commit()
    store_batch(
        session,
        parse_batch(
            nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.4}]), now=JETZT
        ),
    )
    session.commit()

    zeile = session.scalar(select(Measurement))
    assert zeile.temperature_c == pytest.approx(12.4)
    assert zeile.pressure_station_hpa == pytest.approx(985.2)


def test_mehrere_datensaetze_im_stapel(session):
    records = [
        {"t": f"2026-09-10T11:{m:02d}:00Z", "temp": 12.0 + m * 0.1} for m in range(50, 59)
    ]
    anzahl, _ = store_batch(session, parse_batch(nachricht(records), now=JETZT))
    session.commit()
    assert anzahl == 9
    assert session.scalar(select(func.count()).select_from(Measurement)) == 9


def test_erster_zustand_legt_zeitraum_an(session):
    store_batch(
        session,
        parse_batch(
            nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.0}]), now=JETZT
        ),
    )
    session.commit()
    zeile = session.scalar(select(SensorStateRow))
    assert zeile.sensor_key == "bme280"
    assert zeile.state == "productive"
    assert zeile.valid_to is None


def test_unveraenderter_zustand_erzeugt_keine_neue_zeile(session):
    """Sonst stünden bei einer Nachricht alle zwei Minuten Hunderttausende
    identischer Zeilen im Jahr."""
    daten = nachricht([{"t": "2026-09-10T11:58:00Z", "temp": 12.0}])
    for _ in range(5):
        store_batch(session, parse_batch(daten, now=JETZT))
        session.commit()
    assert session.scalar(select(func.count()).select_from(SensorStateRow)) == 1


def test_zustandswechsel_schliesst_den_alten_zeitraum(session):
    """Der Sensor wandert vom Schreibtisch nach draußen."""
    store_batch(
        session,
        parse_batch(
            nachricht(
                [{"t": "2026-09-10T11:58:00Z", "temp": 22.0}],
                sensors={"bme280": "test"},
            ),
            now=JETZT,
        ),
    )
    session.commit()
    store_batch(
        session,
        parse_batch(
            nachricht(
                [{"t": "2026-09-10T12:30:00Z", "temp": 12.0}],
                sensors={"bme280": "productive"},
            ),
            now=JETZT + timedelta(minutes=30),
        ),
    )
    session.commit()

    zeilen = session.scalars(
        select(SensorStateRow).order_by(SensorStateRow.valid_from)
    ).all()
    assert [z.state for z in zeilen] == ["test", "productive"]
    assert zeilen[0].valid_to is not None
    assert zeilen[1].valid_to is None
    # Lückenlos: das Ende des ersten ist der Beginn des zweiten.
    assert zeilen[0].valid_to == zeilen[1].valid_from


def test_abgeklemmter_sensor_wird_inaktiv(session):
    """Meldet die Station einen Sensor nicht mehr, gilt er als abgezogen."""
    store_batch(
        session,
        parse_batch(
            nachricht(
                [{"t": "2026-09-10T11:58:00Z", "temp": 12.0, "sky": -30.0}],
                sensors={"bme280": "productive", "mlx90614": "productive"},
            ),
            now=JETZT,
        ),
    )
    session.commit()
    store_batch(
        session,
        parse_batch(
            nachricht(
                [{"t": "2026-09-10T12:30:00Z", "temp": 12.5}],
                sensors={"bme280": "productive"},
            ),
            now=JETZT + timedelta(minutes=30),
        ),
    )
    session.commit()

    zeilen = session.scalars(
        select(SensorStateRow).where(SensorStateRow.sensor_key == "mlx90614")
    ).all()
    assert [z.state for z in zeilen] == ["productive", "inactive"]


def test_historie_wird_als_intervalle_gelesen(session):
    store_batch(
        session,
        parse_batch(
            nachricht(
                [{"t": "2026-09-10T11:58:00Z", "temp": 22.0}],
                sensors={"bme280": "test"},
            ),
            now=JETZT,
        ),
    )
    session.commit()
    station = session.scalar(select(Station))
    intervalle = load_state_intervals(session, station)
    assert len(intervalle) == 1
    assert intervalle[0].sensor_key == "bme280"
    assert intervalle[0].state is SensorState.TEST
    assert intervalle[0].valid_to is None


def test_zustandsabgleich_meldet_die_aenderungen(session):
    from wetter.ingest.store import get_or_create_station

    station = get_or_create_station(session, "garten")
    geaendert = sync_sensor_states(
        session, station, {"bme280": SensorState.PRODUCTIVE}, at=JETZT
    )
    assert geaendert == ["bme280"]
    geaendert = sync_sensor_states(
        session, station, {"bme280": SensorState.PRODUCTIVE}, at=JETZT
    )
    assert geaendert == []
