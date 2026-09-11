#include "power.h"

#include "esp_adc/adc_cali.h"
#include "esp_adc/adc_cali_scheme.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "esp_pm.h"

static const char *TAG = "power";

/* Mehrfach messen und mitteln: der ADC des ESP32 rauscht deutlich, und die
 * Betriebsart soll nicht an einem einzelnen Ausreisser haengen. */
#define SAMPLES 16

static adc_oneshot_unit_handle_t s_adc;
static adc_cali_handle_t s_cali;
static adc_channel_t s_channel;
static float s_divider = 2.0f;
static bool s_ready;

esp_err_t power_init(int adc_gpio, float divider_ratio)
{
    adc_unit_t unit;
    esp_err_t err = adc_oneshot_io_to_channel(adc_gpio, &unit, &s_channel);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "GPIO %d ist kein ADC-Pin", adc_gpio);
        return err;
    }

    const adc_oneshot_unit_init_cfg_t unit_cfg = {.unit_id = unit};
    err = adc_oneshot_new_unit(&unit_cfg, &s_adc);
    if (err != ESP_OK) {
        return err;
    }

    const adc_oneshot_chan_cfg_t chan_cfg = {
        .bitwidth = ADC_BITWIDTH_DEFAULT,
        /* 12 dB deckt bis rund 3,1 V ab -- mit dem Halbierungsteiler also einen
         * Akku bis gut 6 V. */
        .atten = ADC_ATTEN_DB_12,
    };
    err = adc_oneshot_config_channel(s_adc, s_channel, &chan_cfg);
    if (err != ESP_OK) {
        return err;
    }

    /* Ohne die eingebaute Kalibrierung ist der ADC des ESP32 um mehrere Prozent
     * daneben -- und zwar von Chip zu Chip verschieden. Bei einer Entscheidung,
     * die an 0,2 V haengt, ist das der Unterschied zwischen richtig und still
     * falsch. */
    const adc_cali_line_fitting_config_t cali_cfg = {
        .unit_id = unit,
        .atten = ADC_ATTEN_DB_12,
        .bitwidth = ADC_BITWIDTH_DEFAULT,
    };
    if (adc_cali_create_scheme_line_fitting(&cali_cfg, &s_cali) != ESP_OK) {
        ESP_LOGW(TAG, "ADC-Kalibrierung nicht verfuegbar, Werte sind ungenauer");
        s_cali = NULL;
    }

    s_divider = divider_ratio > 0.0f ? divider_ratio : 2.0f;
    s_ready = true;
    return ESP_OK;
}

float power_battery_volts(void)
{
    if (!s_ready) {
        return -1.0f;
    }

    int summe = 0;
    int gueltig = 0;
    for (int i = 0; i < SAMPLES; ++i) {
        int roh = 0;
        if (adc_oneshot_read(s_adc, s_channel, &roh) == ESP_OK) {
            summe += roh;
            gueltig++;
        }
    }
    if (gueltig == 0) {
        return -1.0f;
    }

    const int mittel = summe / gueltig;
    int millivolt = mittel;
    if (s_cali != NULL) {
        if (adc_cali_raw_to_voltage(s_cali, mittel, &millivolt) != ESP_OK) {
            return -1.0f;
        }
    }
    return ((float)millivolt / 1000.0f) * s_divider;
}

esp_err_t power_enable_light_sleep(int max_mhz, int min_mhz)
{
    const esp_pm_config_t cfg = {
        .max_freq_mhz = max_mhz,
        .min_freq_mhz = min_mhz,
        /* Der eigentliche Sparposten: der Kern schlaeft zwischen den Ereignissen
         * von selbst ein und wird vom Reed-Interrupt oder vom Timer geweckt. */
        .light_sleep_enable = true,
    };
    esp_err_t err = esp_pm_configure(&cfg);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Light-Sleep aktiv, Takt %d bis %d MHz", min_mhz, max_mhz);
    }
    return err;
}
