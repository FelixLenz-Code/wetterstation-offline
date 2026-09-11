/* MQTT-Anbindung der Station.
 *
 * QoS 1 in beide Richtungen. Das ist nicht Vorsicht, sondern die Grundlage des
 * Store-and-Forward: ein Datensatz wird erst aus dem Ringpuffer freigegeben,
 * wenn der Broker ihn bestaetigt hat. Ohne diese Bestaetigung wuesste die
 * Station nie, ob sie einen Messwert wegwerfen darf.
 *
 * Die Kehrseite: derselbe Datensatz kann mehrfach ankommen, wenn die
 * Bestaetigung verlorengeht. Der Server faengt das mit einem Upsert ab.
 */
#ifndef MQTT_LINK_H
#define MQTT_LINK_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct {
    const char *host;
    int port;
    const char *username;
    const char *password;
    const char *station_key;
} mqtt_link_config_t;

/* Rueckruf fuer Befehle vom Server (Sensorzustaende). */
typedef void (*mqtt_command_cb_t)(const char *json, int len);

esp_err_t mqtt_link_init(const mqtt_link_config_t *cfg, mqtt_command_cb_t on_command);
esp_err_t mqtt_link_start(int timeout_ms);
void mqtt_link_stop(void);
bool mqtt_link_connected(void);

/* Veroeffentlicht einen Stapel und wartet auf die Bestaetigung des Brokers.
 * Gibt ESP_OK nur zurueck, wenn das PUBACK da ist -- erst dann darf der
 * Ringpuffer die Datensaetze freigeben. */
esp_err_t mqtt_link_publish_batch(const char *payload, int len, int timeout_ms);

/* Kurzer, dauerhaft gespeicherter Zustand fuer Home Assistant. */
esp_err_t mqtt_link_publish_status(const char *payload, int len);

#endif /* MQTT_LINK_H */
