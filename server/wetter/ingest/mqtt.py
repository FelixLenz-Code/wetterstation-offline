"""MQTT-Anbindung des Ingest.

Zwei Einstellungen sind hier wichtiger, als sie aussehen:

* **Dauerhafte Sitzung** (fester ``client_id``, ``clean_session=False``). Der Broker
  hebt Nachrichten auf, während der Server steht -- ein Neustart des Ingest kostet
  dann keine Messwerte. Mit einer flüchtigen Sitzung wäre der Ringpuffer der Station
  umsonst gebaut, weil die Lücke einfach eine Stufe später entstünde.
* **QoS 1 in beide Richtungen.** Die Station gibt einen Datensatz erst nach dem
  PUBACK des Brokers frei. Das heisst zugleich: derselbe Datensatz kann mehrfach
  ankommen, wenn die Bestätigung verloren geht. Der Upsert im Store fängt das ab.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

import paho.mqtt.client as mqtt
from pydantic_settings import BaseSettings, SettingsConfigDict

from wetter.ingest import topics

log = logging.getLogger(__name__)


class MqttSettings(BaseSettings):
    """Verbindungsdaten aus der Umgebung."""

    model_config = SettingsConfigDict(env_prefix="WETTER_MQTT_", extra="ignore")

    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    client_id: str = "wetter-ingest"
    """Fest, nicht zufällig -- sonst findet der Broker die aufgehobene Sitzung nicht."""

    keepalive: int = 60
    ha_discovery: bool = True


@dataclass
class Message:
    topic: str
    station_key: str | None
    payload: dict


class IngestClient:
    """Abonniert die Messstapel und reicht sie an einen Rückruf weiter.

    Der Rückruf bekommt eine bereits als JSON geparste Nachricht. Wirft er, wird die
    Nachricht *nicht* bestätigt -- der Broker liefert sie erneut. Das ist gewollt:
    ein Fehler in der Datenbank darf keine Messwerte verschlucken.
    """

    def __init__(
        self,
        settings: MqttSettings,
        on_batch: Callable[[Message], None],
    ) -> None:
        self.settings = settings
        self.on_batch = on_batch
        self._connected = threading.Event()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=settings.client_id,
            clean_session=False,
            protocol=mqtt.MQTTv311,
        )
        if settings.username:
            self.client.username_pw_set(settings.username, settings.password)

        # Nachrichten der Reihe nach verarbeiten. Parallel wäre schneller, würde aber
        # die Sprungprüfung über Stapelgrenzen hinweg durcheinanderbringen.
        self.client.max_inflight_messages_set(1)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            log.error("MQTT-Verbindung abgelehnt: %s", reason_code)
            return
        log.info(
            "MQTT verbunden mit %s:%s (Sitzung %s)",
            self.settings.host,
            self.settings.port,
            "fortgesetzt" if flags.session_present else "neu",
        )
        client.subscribe(topics.subscription(), qos=1)
        self._connected.set()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._connected.clear()
        if reason_code != 0:
            # Kein Grund zur Panik: paho verbindet selbst neu, und der Broker hat
            # die Nachrichten aufgehoben.
            log.warning("MQTT-Verbindung verloren (%s), versuche erneut", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            nutzlast = json.loads(msg.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            # Unlesbares wird bestätigt und verworfen -- sonst liefert der Broker es
            # endlos erneut und blockiert alles dahinter.
            log.error("Unlesbare Nachricht auf %s: %s", msg.topic, exc)
            return
        try:
            self.on_batch(
                Message(
                    topic=msg.topic,
                    station_key=topics.station_from_topic(msg.topic),
                    payload=nutzlast,
                )
            )
        except Exception:
            log.exception("Verarbeitung von %s fehlgeschlagen", msg.topic)
            raise

    def publish_discovery(self, station_key: str, station_name: str) -> None:
        """Meldet die Sensoren bei Home Assistant an."""
        if not self.settings.ha_discovery:
            return
        for thema, nutzlast in topics.discovery_messages(station_key, station_name):
            self.client.publish(thema, nutzlast, qos=1, retain=True)
        log.info("Home-Assistant-Discovery für %s veröffentlicht", station_key)

    def send_command(self, station_key: str, command: dict) -> None:
        """Schickt Konfiguration an die Station, etwa einen Sensorzustand."""
        self.client.publish(
            topics.command_topic(station_key),
            json.dumps(command, ensure_ascii=False),
            qos=1,
            retain=True,
        )

    def connect(self) -> None:
        self.client.connect_async(
            self.settings.host, self.settings.port, self.settings.keepalive
        )
        self.client.loop_start()

    def wait_connected(self, timeout: float = 30.0) -> bool:
        return self._connected.wait(timeout)

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()
