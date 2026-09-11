#include "timesync.h"

#include <sys/time.h>
#include <time.h>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "zeit";

/* Alles vor 2024 kann nur der Startwert sein. */
#define STICHTAG 1704067200LL

static bool s_gestartet;

esp_err_t timesync_start(const char *server)
{
    if (s_gestartet) {
        return ESP_OK;
    }
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG(server);
    /* Alle paar Stunden nachziehen: der externe Quarz driftet mit rund 20 ppm,
     * also gut einer Sekunde am Tag. Fuer Stundenwerte unkritisch, aber es
     * kostet nichts. */
    cfg.sync_cb = NULL;
    esp_err_t err = esp_netif_sntp_init(&cfg);
    if (err == ESP_OK) {
        s_gestartet = true;
        ESP_LOGI(TAG, "Zeitabgleich gegen %s", server);
    }
    return err;
}

bool timesync_wait(int timeout_ms)
{
    if (!s_gestartet) {
        return false;
    }
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(timeout_ms)) != ESP_OK) {
        ESP_LOGW(TAG, "kein Zeitabgleich -- Messungen warten");
        return false;
    }
    return timesync_valid();
}

bool timesync_valid(void)
{
    return timesync_now() > STICHTAG;
}

int64_t timesync_now(void)
{
    struct timeval tv;
    if (gettimeofday(&tv, NULL) != 0) {
        return 0;
    }
    return (int64_t)tv.tv_sec;
}
