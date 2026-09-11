"""Tests der Befehlszustellung an die Station.

Der Umweg über eine Warteschlange hat einen Grund: schriebe die Oberfläche den
Sensorzustand direkt in die Datenbank, wüsste diese etwas, das die Station nicht
weiss. Beim nächsten Neustart meldete das Gerät seinen alten Zustand -- und die
Anzeige hätte still gelogen.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from wetter.worker.commands import Command, deliver, pending, validate


@pytest.fixture(autouse=True)
def app_schema(session):
    """Überspringt die Tests, wenn das Schema der Oberfläche fehlt."""
    vorhanden = session.execute(
        text("SELECT count(*) FROM pg_tables WHERE schemaname='app' "
             "AND tablename='station_command'")
    ).scalar_one()
    if not vorhanden:
        pytest.skip("Prisma-Migrationen nicht vorhanden")


def befehl_anlegen(session, sensor="bme280", zustand="productive", station="garten"):
    kennung = f"c{datetime.now(UTC).timestamp()}{sensor}{zustand}"
    session.execute(
        text(
            'INSERT INTO app.station_command (id, "stationKey", "sensorKey", '
            '"desiredState", "createdAt", state) '
            "VALUES (:id, :st, :se, :zu, :jetzt, 'PENDING')"
        ),
        {"id": kennung, "st": station, "se": sensor, "zu": zustand,
         "jetzt": datetime.now(UTC)},
    )
    session.flush()
    return kennung


def zustand_von(session, kennung) -> tuple[str, str | None]:
    return session.execute(
        text("SELECT state, note FROM app.station_command WHERE id = :id"),
        {"id": kennung},
    ).one()


def test_offener_befehl_wird_gelesen(session):
    befehl_anlegen(session)
    offen = pending(session)
    assert len(offen) == 1
    assert offen[0].sensor_key == "bme280"
    assert offen[0].desired_state == "productive"


def test_zustellung_markiert_als_gesendet(session):
    kennung = befehl_anlegen(session, sensor="mlx90614", zustand="test")
    gesendet: list[tuple[str, dict]] = []

    zugestellt, abgelehnt = deliver(
        session, lambda st, n: (gesendet.append((st, n)), True)[1]
    )
    session.flush()

    assert (zugestellt, abgelehnt) == (1, 0)
    assert gesendet == [("garten", {"sensors": {"mlx90614": "test"}})]
    assert zustand_von(session, kennung)[0] == "SENT"


def test_nicht_gesendeter_befehl_bleibt_offen(session):
    """Genau dafür ist die Warteschlange da: eine offline Station verliert nichts."""
    kennung = befehl_anlegen(session)
    zugestellt, abgelehnt = deliver(session, lambda st, n: False)
    session.flush()

    assert (zugestellt, abgelehnt) == (0, 0)
    assert zustand_von(session, kennung)[0] == "PENDING"
    # Beim nächsten Versuch klappt es.
    deliver(session, lambda st, n: True)
    session.flush()
    assert zustand_von(session, kennung)[0] == "SENT"


def test_unbekannter_sensor_wird_abgelehnt(session):
    """Auf dem Gerät täte ein unbekannter Name stillschweigend nichts."""
    kennung = befehl_anlegen(session, sensor="gibtsnicht")
    zugestellt, abgelehnt = deliver(session, lambda st, n: True)
    session.flush()

    assert (zugestellt, abgelehnt) == (0, 1)
    zustand, grund = zustand_von(session, kennung)
    assert zustand == "FAILED"
    assert "gibtsnicht" in grund


def test_unbekannter_zustand_wird_abgelehnt(session):
    kennung = befehl_anlegen(session, zustand="vielleicht")
    deliver(session, lambda st, n: True)
    session.flush()
    zustand, grund = zustand_von(session, kennung)
    assert zustand == "FAILED"
    assert "vielleicht" in grund


def test_gesendete_befehle_werden_nicht_erneut_zugestellt(session):
    befehl_anlegen(session)
    deliver(session, lambda st, n: True)
    session.flush()
    assert deliver(session, lambda st, n: True) == (0, 0)


def test_aelteste_zuerst(session):
    """Sonst überholte ein später Befehl einen früheren und der alte gewänne."""
    import time

    erste = befehl_anlegen(session, sensor="bme280", zustand="test")
    time.sleep(0.01)
    zweite = befehl_anlegen(session, sensor="bme280", zustand="productive")
    reihenfolge = [b.id for b in pending(session)]
    assert reihenfolge == [erste, zweite]


def test_pruefung_einzeln():
    gut = Command("x", "garten", "bme280", "test", datetime.now(UTC))
    assert validate(gut) is None
    ohne_sensor = Command("x", "g", None, "test", datetime.now(UTC))
    assert "kein Sensor" in (validate(ohne_sensor) or "")
