"""Befehle der Weboberfläche an die Station zustellen.

Warum der Umweg über eine Warteschlange, statt dass die Oberfläche den
Sensorzustand direkt in ``wetter.sensor_state`` schreibt: dann wüsste die Datenbank
etwas, das die Station nicht weiss. Beim nächsten Neustart meldete das Gerät seinen
alten Zustand, und die Anzeige hätte still gelogen.

So läuft es stattdessen: die Oberfläche legt einen Befehl ab, der Worker schickt ihn
per MQTT, die Station übernimmt ihn und meldet ihren neuen Zustand zurück -- der
Ingest schreibt ihn dann auf dem gewohnten Weg in die Historie. Es gibt weiterhin
genau einen Weg, auf dem ein Sensorzustand in die Datenbank kommt.

Der Nebeneffekt rechtfertigt den Aufwand allein: ein Befehl überlebt einen Neustart
und eine Station, die gerade offline ist.

Gelesen wird die Tabelle mit rohem SQL. Sie liegt im Schema ``app``, das Prisma
gehört -- ein SQLAlchemy-Modell dafür wäre eine zweite Wahrheit über dieselbe
Tabelle und müsste bei jeder Prisma-Migration nachgezogen werden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from wetter.db.sensors import BY_KEY, SensorState

log = logging.getLogger(__name__)

#: Soviele Befehle werden je Durchgang zugestellt. Mehr wäre sinnlos -- die Station
#: meldet sich ohnehin nur alle paar Minuten.
BATCH = 50

#: Nach so vielen Fehlversuchen gilt ein Befehl als gescheitert. Ohne die Grenze
#: würde ein Befehl an eine längst abgebaute Station ewig wiederholt.
MAX_VERSUCHE = 20


@dataclass
class Command:
    id: str
    station_key: str
    sensor_key: str | None
    desired_state: str | None
    created_at: datetime


def pending(session: Session, *, limit: int = BATCH) -> list[Command]:
    """Liest offene Befehle, älteste zuerst."""
    zeilen = session.execute(
        text(
            'SELECT id, "stationKey", "sensorKey", "desiredState", "createdAt" '
            "FROM app.station_command WHERE state = 'PENDING' "
            'ORDER BY "createdAt" LIMIT :limit'
        ),
        {"limit": limit},
    ).all()
    return [
        Command(
            id=z[0],
            station_key=z[1],
            sensor_key=z[2],
            desired_state=z[3],
            created_at=z[4],
        )
        for z in zeilen
    ]


def mark_sent(session: Session, command_id: str) -> None:
    session.execute(
        text(
            "UPDATE app.station_command "
            "SET state = 'SENT', \"sentAt\" = :jetzt WHERE id = :id"
        ),
        {"id": command_id, "jetzt": datetime.now(UTC)},
    )


def mark_failed(session: Session, command_id: str, grund: str) -> None:
    session.execute(
        text(
            "UPDATE app.station_command "
            "SET state = 'FAILED', note = :grund WHERE id = :id"
        ),
        {"id": command_id, "grund": grund[:500]},
    )


def validate(command: Command) -> str | None:
    """Prüft einen Befehl. Gibt bei Ablehnung die Begründung zurück.

    Die Oberfläche prüft schon, aber sie ist nicht die einzige mögliche Quelle --
    und ein unbekannter Sensorname würde auf dem Gerät stillschweigend nichts tun.
    """
    if not command.sensor_key:
        return "kein Sensor angegeben"
    if command.sensor_key not in BY_KEY:
        return f"unbekannter Sensor {command.sensor_key!r}"
    try:
        SensorState(command.desired_state)
    except ValueError:
        return f"unbekannter Zustand {command.desired_state!r}"
    return None


def deliver(session: Session, publish, *, limit: int = BATCH) -> tuple[int, int]:
    """Stellt offene Befehle zu.

    ``publish`` bekommt Stationsschlüssel und Nutzlast und meldet mit ``True``,
    dass der Broker die Nachricht angenommen hat. Erst dann gilt der Befehl als
    zugestellt -- bei einem abgelehnten Senden bleibt er liegen und wird beim
    nächsten Durchgang erneut versucht.

    Gibt die Zahl der zugestellten und der abgelehnten Befehle zurück.
    """
    zugestellt = 0
    abgelehnt = 0

    for befehl in pending(session, limit=limit):
        grund = validate(befehl)
        if grund is not None:
            mark_failed(session, befehl.id, grund)
            abgelehnt += 1
            log.warning("Befehl %s abgelehnt: %s", befehl.id, grund)
            continue

        nutzlast = {"sensors": {befehl.sensor_key: befehl.desired_state}}
        if publish(befehl.station_key, nutzlast):
            mark_sent(session, befehl.id)
            zugestellt += 1
            log.info(
                "Befehl an %s zugestellt: %s -> %s",
                befehl.station_key,
                befehl.sensor_key,
                befehl.desired_state,
            )
        else:
            # Kein Vermerk: der Befehl bleibt offen und wird erneut versucht. Genau
            # dafür ist die Warteschlange da.
            log.debug("Befehl %s konnte nicht gesendet werden", befehl.id)

    return zugestellt, abgelehnt
