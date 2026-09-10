"""Einstiegspunkt des Ingest-Dienstes."""

from __future__ import annotations

import logging
import signal
import sys
import threading

from wetter.db.session import make_engine, make_session_factory, session_scope
from wetter.ingest.mqtt import IngestClient, Message, MqttSettings
from wetter.ingest.payload import PayloadError, parse_batch
from wetter.ingest.store import store_batch

log = logging.getLogger("wetter.ingest")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    engine = make_engine()
    factory = make_session_factory(engine)
    beenden = threading.Event()

    def verarbeite(msg: Message) -> None:
        try:
            batch = parse_batch(msg.payload)
        except PayloadError as exc:
            # Kaputte Nachricht: melden und verwerfen. Ein erneuter Versuch würde
            # dasselbe Ergebnis liefern und die Warteschlange blockieren.
            log.error("Nachricht auf %s unbrauchbar: %s", msg.topic, exc)
            return

        if msg.station_key and batch.station_key != msg.station_key:
            log.warning(
                "Station im Thema (%s) und in der Nachricht (%s) stimmen nicht überein",
                msg.station_key,
                batch.station_key,
            )

        with session_scope(factory) as session:
            anzahl, geaendert = store_batch(session, batch)
        log.info(
            "%s: %d Messwerte gespeichert%s",
            batch.station_key,
            anzahl,
            f", Zustandswechsel: {', '.join(geaendert)}" if geaendert else "",
        )

    client = IngestClient(MqttSettings(), verarbeite)
    client.connect()
    if not client.wait_connected():
        log.error("Keine Verbindung zum MQTT-Broker")
        return 1

    def stoppen(*_):
        log.info("Beende Ingest")
        beenden.set()

    signal.signal(signal.SIGTERM, stoppen)
    signal.signal(signal.SIGINT, stoppen)
    beenden.wait()
    client.stop()
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
