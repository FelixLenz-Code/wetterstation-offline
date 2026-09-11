"""Der Worker: Rollup, Vorhersage, Verifikation und nächtliches Training.

Ein einfacher Schleifendienst statt eines Cron-Aufrufs. Grund: die Jobs hängen
voneinander ab (erst Rollup, dann Vorhersage, dann Verifikation) und teilen sich
teure Zustände wie die geladene Klimatologie. Getrennte Cron-Jobs müssten die jedes
Mal neu aufbauen und könnten sich obendrein überholen.

Jeder Job fängt seine Fehler selbst ab. Ein Fehler im Training darf nicht dazu
führen, dass auch keine Messwerte mehr verdichtet werden -- das Verdichten ist der
Teil, bei dem Daten verloren gingen.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import select

from wetter.db.models import Station
from wetter.db.session import make_engine, make_session_factory, session_scope
from wetter.dwd.store import dwd_coverage, load_dwd_hourly
from wetter.features.climatology import Climatology
from wetter.ingest.mqtt import MqttSettings
from wetter.ingest.store import load_state_intervals
from wetter.ingest.topics import command_topic
from wetter.worker.commands import deliver
from wetter.worker.forecast import run_forecasts
from wetter.worker.promotion import evaluate_shadow
from wetter.worker.rollup import pending_range, rollup
from wetter.worker.training import needs_training, train_for_station
from wetter.worker.verify import verify_pending

log = logging.getLogger("wetter.worker")

#: Takte der einzelnen Jobs.
ROLLUP_INTERVAL = timedelta(minutes=5)
COMMAND_INTERVAL = timedelta(seconds=30)
FORECAST_INTERVAL = timedelta(minutes=10)
VERIFY_INTERVAL = timedelta(hours=1)

#: Uhrzeit des nächtlichen Trainings (UTC). 02:00 UTC ist in Deutschland 03:00 bzw.
#: 04:00 Ortszeit -- gerechnet wird also, wenn niemand hinsieht.
TRAINING_HOUR = time(2, 0)

MODEL_ROOT = Path(os.environ.get("WETTER_MODEL_ROOT", "/var/lib/wetter/models"))


class Worker:
    """Hält den Zustand zwischen den Läufen."""

    def __init__(self, factory, *, model_root: Path = MODEL_ROOT, mqtt=None) -> None:
        self.factory = factory
        self.model_root = model_root
        self.mqtt = mqtt
        self.last_commands = datetime.min.replace(tzinfo=UTC)
        self._climatology: Climatology | None = None
        self._climatology_at: datetime | None = None
        self.last_rollup = datetime.min.replace(tzinfo=UTC)
        self.last_forecast = datetime.min.replace(tzinfo=UTC)
        self.last_verify = datetime.min.replace(tzinfo=UTC)
        self.last_training_day: str | None = None

    def climatology(self, session) -> Climatology | None:
        """Klimatologie, einmal am Tag neu gerechnet.

        Die Berechnung läuft über Jahrzehnte Stundenwerte und dauert mehrere
        Sekunden. Sie ändert sich mit einem neuen Tag praktisch nicht, aber im
        Zehn-Minuten-Takt wäre sie die teuerste Rechnung im ganzen Dienst.
        """
        jetzt = datetime.now(UTC)
        frisch = (
            self._climatology_at is not None
            and (jetzt - self._climatology_at) < timedelta(days=1)
        )
        if self._climatology is not None and frisch:
            return self._climatology

        abdeckung = dwd_coverage(session)
        if abdeckung is None:
            return None
        dwd = load_dwd_hourly(session)
        if dwd.empty:
            return None
        self._climatology = Climatology.from_hourly(dwd)
        self._climatology_at = jetzt
        log.info("Klimatologie aus %.1f Jahren neu gerechnet", self._climatology.years)
        return self._climatology

    def stations(self, session) -> list[Station]:
        return list(session.scalars(select(Station)).all())

    def do_rollup(self) -> None:
        with session_scope(self.factory) as session:
            for station in self.stations(session):
                bereich = pending_range(session, station)
                if bereich is None:
                    continue
                intervalle = load_state_intervals(session, station)
                rollup(session, station, intervalle, start=bereich[0], end=bereich[1])

    def do_forecast(self) -> None:
        with session_scope(self.factory) as session:
            klima = self.climatology(session)
            for station in self.stations(session):
                _, hinweise = run_forecasts(session, station, climatology=klima)
                for h in hinweise:
                    log.warning("%s: %s", station.key, h)

    def do_verify(self) -> None:
        with session_scope(self.factory) as session:
            verify_pending(session)
            evaluate_shadow(session)

    def do_commands(self) -> None:
        """Stellt Befehle der Oberfläche an die Station zu."""
        if self.mqtt is None:
            return

        def sende(station_key: str, nutzlast: dict) -> bool:
            info = self.mqtt.publish(
                command_topic(station_key),
                json.dumps(nutzlast, ensure_ascii=False),
                qos=1,
                retain=True,
            )
            # rc == 0 heisst, der Client hat die Nachricht angenommen. Bei allem
            # anderen bleibt der Befehl offen und wird erneut versucht.
            return info.rc == 0

        with session_scope(self.factory) as session:
            deliver(session, sende)

    def do_training(self) -> None:
        with session_scope(self.factory) as session:
            if not needs_training(session):
                log.info("Training übersprungen -- heute schon gelaufen")
                return
            for station in self.stations(session):
                bericht = train_for_station(
                    session, station, root=self.model_root
                )
                for grund in bericht.skipped:
                    log.warning("%s: %s", station.key, grund)

    def tick(self, now: datetime) -> None:
        """Führt aus, was gerade dran ist. Jeder Job kapselt seine Fehler."""
        for name, letzte, takt, job in (
            ("Rollup", self.last_rollup, ROLLUP_INTERVAL, self.do_rollup),
            ("Vorhersage", self.last_forecast, FORECAST_INTERVAL, self.do_forecast),
            ("Verifikation", self.last_verify, VERIFY_INTERVAL, self.do_verify),
            ("Befehle", self.last_commands, COMMAND_INTERVAL, self.do_commands),
        ):
            if now - letzte < takt:
                continue
            try:
                job()
            except Exception:
                log.exception("%s fehlgeschlagen", name)
            # Auch nach einem Fehler weiterzählen, sonst läuft der Job in einer
            # Schleife ununterbrochen und verdrängt alles andere.
            if name == "Rollup":
                self.last_rollup = now
            elif name == "Vorhersage":
                self.last_forecast = now
            elif name == "Verifikation":
                self.last_verify = now
            else:
                self.last_commands = now

        heute = now.date().isoformat()
        if now.time() >= TRAINING_HOUR and self.last_training_day != heute:
            self.last_training_day = heute
            try:
                self.do_training()
            except Exception:
                log.exception("Training fehlgeschlagen")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    engine = make_engine()
    factory = make_session_factory(engine)

    # Der Worker braucht MQTT nur zum Senden, nicht zum Empfangen -- das macht der
    # Ingest. Eine eigene Verbindung mit eigenem client_id, damit die beiden sich
    # nicht gegenseitig vom Broker werfen.
    import paho.mqtt.client as mqtt_client

    einstellungen = MqttSettings(client_id="wetter-worker")
    sender = mqtt_client.Client(
        mqtt_client.CallbackAPIVersion.VERSION2,
        client_id=einstellungen.client_id,
        protocol=mqtt_client.MQTTv311,
    )
    if einstellungen.username:
        sender.username_pw_set(einstellungen.username, einstellungen.password)
    try:
        sender.connect_async(
            einstellungen.host, einstellungen.port, einstellungen.keepalive
        )
        sender.loop_start()
    except OSError:
        # Ohne Broker laufen Rollup, Vorhersage und Training weiter -- nur Befehle
        # bleiben liegen. Das ist die richtige Reihenfolge der Prioritäten.
        log.warning("Kein MQTT-Broker erreichbar, Befehle bleiben in der Warteschlange")

    worker = Worker(factory, mqtt=sender)
    worker.model_root.mkdir(parents=True, exist_ok=True)

    beenden = threading.Event()

    def stoppen(*_):
        log.info("Beende Worker")
        beenden.set()

    signal.signal(signal.SIGTERM, stoppen)
    signal.signal(signal.SIGINT, stoppen)

    log.info("Worker gestartet, Modelle unter %s", worker.model_root)
    while not beenden.is_set():
        worker.tick(datetime.now(UTC))
        # Kurz genug, um auf SIGTERM zügig zu reagieren.
        beenden.wait(30)

    sender.loop_stop()
    sender.disconnect()
    engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
