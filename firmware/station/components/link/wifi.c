#include "wifi.h"

#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

static const char *TAG = "wifi";

#define BIT_VERBUNDEN BIT0
#define BIT_FEHLER BIT1

/* Soviele Versuche, bevor aufgegeben wird. Aufgeben ist hier kein Drama: die
 * Daten liegen im Ringpuffer und der naechste Sendeversuch holt sie nach. */
#define MAX_VERSUCHE 5

static EventGroupHandle_t s_events;
static esp_netif_t *s_netif;
static int s_versuche;
static bool s_verbunden;
static bool s_init;

static void ereignis(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)data;

    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_verbunden = false;
        if (s_versuche < MAX_VERSUCHE) {
            s_versuche++;
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(s_events, BIT_FEHLER);
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_versuche = 0;
        s_verbunden = true;
        xEventGroupSetBits(s_events, BIT_VERBUNDEN);
    }
}

esp_err_t wifi_init(const char *ssid, const char *password)
{
    if (s_init) {
        return ESP_OK;
    }
    if (ssid == NULL || password == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_events = xEventGroupCreate();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_netif = esp_netif_create_default_wifi_sta();

    const wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &ereignis, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &ereignis, NULL, NULL));

    wifi_config_t wcfg = {0};
    strncpy((char *)wcfg.sta.ssid, ssid, sizeof(wcfg.sta.ssid) - 1);
    strncpy((char *)wcfg.sta.password, password, sizeof(wcfg.sta.password) - 1);
    wcfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wcfg));

    /* Der Empfaenger darf zwischen den Beacons schlafen. Das ist der groesste
     * einzelne Sparposten, solange die Verbindung steht. */
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_MAX_MODEM));

    s_init = true;
    return ESP_OK;
}

esp_err_t wifi_connect(int timeout_ms)
{
    if (!s_init) {
        return ESP_ERR_INVALID_STATE;
    }
    if (s_verbunden) {
        return ESP_OK;
    }

    s_versuche = 0;
    xEventGroupClearBits(s_events, BIT_VERBUNDEN | BIT_FEHLER);
    ESP_ERROR_CHECK(esp_wifi_start());

    const EventBits_t bits =
        xEventGroupWaitBits(s_events, BIT_VERBUNDEN | BIT_FEHLER, pdFALSE, pdFALSE,
                            pdMS_TO_TICKS(timeout_ms));

    if (bits & BIT_VERBUNDEN) {
        ESP_LOGI(TAG, "verbunden, %d dBm", wifi_rssi());
        return ESP_OK;
    }
    ESP_LOGW(TAG, "keine Verbindung -- Daten bleiben im Ringpuffer");
    esp_wifi_stop();
    return ESP_ERR_TIMEOUT;
}

void wifi_disconnect(void)
{
    if (!s_init || !s_verbunden) {
        return;
    }
    esp_wifi_disconnect();
    esp_wifi_stop();
    s_verbunden = false;
}

bool wifi_connected(void)
{
    return s_verbunden;
}

int wifi_rssi(void)
{
    if (!s_verbunden) {
        return 0;
    }
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
        return 0;
    }
    return ap.rssi;
}
