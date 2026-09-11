#include "mqtt_link.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "mqtt_client.h"

static const char *TAG = "mqtt";

#define BIT_VERBUNDEN BIT0
#define BIT_BESTAETIGT BIT1

static esp_mqtt_client_handle_t s_client;
static EventGroupHandle_t s_events;
static mqtt_command_cb_t s_on_command;
static bool s_verbunden;
static int s_warte_auf_msg_id = -1;

static char s_topic_batch[96];
static char s_topic_status[96];
static char s_topic_cmd[96];

static void ereignis(void *handler_args, esp_event_base_t base, int32_t id,
                     void *event_data)
{
    (void)handler_args;
    (void)base;
    esp_mqtt_event_handle_t e = (esp_mqtt_event_handle_t)event_data;

    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED:
        s_verbunden = true;
        esp_mqtt_client_subscribe(s_client, s_topic_cmd, 1);
        xEventGroupSetBits(s_events, BIT_VERBUNDEN);
        break;

    case MQTT_EVENT_DISCONNECTED:
        s_verbunden = false;
        xEventGroupClearBits(s_events, BIT_VERBUNDEN);
        break;

    case MQTT_EVENT_PUBLISHED:
        /* Das PUBACK. Nur wenn es zur gerade gesendeten Nachricht gehoert, darf
         * der Ringpuffer freigeben. */
        if (e->msg_id == s_warte_auf_msg_id) {
            xEventGroupSetBits(s_events, BIT_BESTAETIGT);
        }
        break;

    case MQTT_EVENT_DATA:
        if (s_on_command != NULL && e->data_len > 0) {
            s_on_command(e->data, e->data_len);
        }
        break;

    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "Fehler im MQTT-Client");
        break;

    default:
        break;
    }
}

esp_err_t mqtt_link_init(const mqtt_link_config_t *cfg, mqtt_command_cb_t on_command)
{
    if (cfg == NULL || cfg->host == NULL || cfg->station_key == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    snprintf(s_topic_batch, sizeof(s_topic_batch), "wetter/station/%s/batch",
             cfg->station_key);
    snprintf(s_topic_status, sizeof(s_topic_status), "wetter/station/%s/status",
             cfg->station_key);
    snprintf(s_topic_cmd, sizeof(s_topic_cmd), "wetter/station/%s/cmd",
             cfg->station_key);

    char uri[128];
    snprintf(uri, sizeof(uri), "mqtt://%s:%d", cfg->host, cfg->port);

    const esp_mqtt_client_config_t mcfg = {
        .broker.address.uri = uri,
        .credentials.username = cfg->username,
        .credentials.authentication.password = cfg->password,
        .credentials.client_id = cfg->station_key,
        /* Dauerhafte Sitzung: der Broker hebt Befehle auf, waehrend die Station
         * schlaeft. Ohne sie ginge eine Zustandsaenderung verloren, die jemand
         * in der Oberflaeche gesetzt hat. */
        .session.disable_clean_session = true,
        .session.keepalive = 120,
        .network.timeout_ms = 10000,
        /* Nicht endlos neu verbinden: die Station soll lieber schlafen und es
         * beim naechsten Sendetakt erneut versuchen. */
        .network.reconnect_timeout_ms = 5000,
        .network.disable_auto_reconnect = true,
    };

    s_events = xEventGroupCreate();
    s_on_command = on_command;
    s_client = esp_mqtt_client_init(&mcfg);
    if (s_client == NULL) {
        return ESP_FAIL;
    }
    return esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, ereignis, NULL);
}

esp_err_t mqtt_link_start(int timeout_ms)
{
    if (s_client == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    xEventGroupClearBits(s_events, BIT_VERBUNDEN);
    esp_err_t err = esp_mqtt_client_start(s_client);
    if (err != ESP_OK) {
        return err;
    }

    const EventBits_t bits = xEventGroupWaitBits(s_events, BIT_VERBUNDEN, pdFALSE,
                                                 pdTRUE, pdMS_TO_TICKS(timeout_ms));
    return (bits & BIT_VERBUNDEN) ? ESP_OK : ESP_ERR_TIMEOUT;
}

void mqtt_link_stop(void)
{
    if (s_client == NULL) {
        return;
    }
    esp_mqtt_client_stop(s_client);
    s_verbunden = false;
}

bool mqtt_link_connected(void)
{
    return s_verbunden;
}

esp_err_t mqtt_link_publish_batch(const char *payload, int len, int timeout_ms)
{
    if (s_client == NULL || payload == NULL || len <= 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_verbunden) {
        return ESP_ERR_INVALID_STATE;
    }

    xEventGroupClearBits(s_events, BIT_BESTAETIGT);
    const int msg_id = esp_mqtt_client_publish(s_client, s_topic_batch, payload, len,
                                               1 /* QoS 1 */, 0 /* nicht retained */);
    if (msg_id < 0) {
        return ESP_FAIL;
    }
    s_warte_auf_msg_id = msg_id;

    const EventBits_t bits = xEventGroupWaitBits(s_events, BIT_BESTAETIGT, pdTRUE,
                                                 pdTRUE, pdMS_TO_TICKS(timeout_ms));
    s_warte_auf_msg_id = -1;

    if (bits & BIT_BESTAETIGT) {
        return ESP_OK;
    }
    /* Ohne Bestaetigung bleiben die Datensaetze im Puffer. Lieber doppelt
     * zustellen als verlieren. */
    ESP_LOGW(TAG, "keine Bestaetigung -- Datensaetze bleiben im Puffer");
    return ESP_ERR_TIMEOUT;
}

esp_err_t mqtt_link_publish_status(const char *payload, int len)
{
    if (s_client == NULL || !s_verbunden) {
        return ESP_ERR_INVALID_STATE;
    }
    /* Retained: Home Assistant und die Oberflaeche sollen den Zustand auch dann
     * sehen, wenn sie erst nach der Station online kommen. */
    const int msg_id =
        esp_mqtt_client_publish(s_client, s_topic_status, payload, len, 1, 1);
    return msg_id < 0 ? ESP_FAIL : ESP_OK;
}
