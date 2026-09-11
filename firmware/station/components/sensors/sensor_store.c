#include "sensor_store.h"

#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "sensor_store";

#define NS "wetter"
#define KEY "sensors"

esp_err_t sensor_store_load(sensor_registry_t *reg)
{
    if (reg == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;
    }

    uint16_t gepackt = 0;
    err = nvs_get_u16(h, KEY, &gepackt);
    nvs_close(h);

    if (err != ESP_OK) {
        return err;
    }
    sensor_registry_unpack(reg, gepackt);
    ESP_LOGI(TAG, "Sensorzustaende geladen (0x%04X)", gepackt);
    return ESP_OK;
}

esp_err_t sensor_store_save(const sensor_registry_t *reg)
{
    if (reg == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    nvs_handle_t h;
    esp_err_t err = nvs_open(NS, NVS_READWRITE, &h);
    if (err != ESP_OK) {
        return err;
    }

    const uint16_t gepackt = sensor_registry_pack(reg);

    /* Nur schreiben, wenn sich wirklich etwas geaendert hat. NVS haelt viel aus,
     * aber ein Schreibvorgang bei jeder Nachricht waere in ein paar Jahren die
     * erste Stelle, die aufgibt. */
    uint16_t vorher = 0;
    if (nvs_get_u16(h, KEY, &vorher) == ESP_OK && vorher == gepackt) {
        nvs_close(h);
        return ESP_OK;
    }

    err = nvs_set_u16(h, KEY, gepackt);
    if (err == ESP_OK) {
        err = nvs_commit(h);
    }
    nvs_close(h);

    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Sensorzustaende gespeichert (0x%04X)", gepackt);
    }
    return err;
}
