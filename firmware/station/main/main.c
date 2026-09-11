/* Hauptprogramm der Aussenstation.
 *
 * Ablauf: messen, in den Ringpuffer schreiben, gelegentlich senden. Die
 * Reihenfolge ist Absicht -- geschrieben wird immer, gesendet nur, wenn es geht.
 * Faellt das WLAN aus, laeuft die Messung weiter und der Puffer holt spaeter auf.
 *
 * Die Station haengt an einem Mast und laesst sich nicht eben neu starten.
 * Deshalb gilt durchgehend: ein Fehler an einer Stelle darf nicht den Rest
 * mitreissen. Ein stummer Sensor liefert NaN, ein ausgefallenes WLAN einen
 * vollen Puffer -- aber die Station misst weiter.
 */

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "bme280.h"
#include "config.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "flash_backend.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2c_bus.h"
#include "mqtt_link.h"
#include "nvs_flash.h"
#include "power.h"
#include "pulse_input.h"
#include "ringbuffer.h"
#include "sensor_registry.h"
#include "sensor_store.h"
#include "timesync.h"
#include "wifi.h"

static const char *TAG = "wetter";

/* Ein Datensatz im Ringpuffer. Gepackt, weil jedes Byte den Puffer kuerzer
 * macht: 28 Byte Nutzlast ergeben bei 1 MB Partition rund 32.000 Datensaetze,
 * also gut drei Wochen im Minutentakt.
 *
 * Messwerte als float: die Genauigkeit reicht weit ueber das hinaus, was die
 * Sensoren koennen, und spart gegenueber double die Haelfte. */
typedef struct __attribute__((packed)) {
    uint32_t unix_time;
    float temperature_c;
    float humidity_pct;
    float pressure_hpa;
    float wind_speed_ms;
    float wind_gust_ms;
    float rain_mm;
    uint16_t wind_dir_deg;  /* 0xFFFF heisst: keine gueltige Richtung */
    uint16_t sensor_states; /* gepackte Zustaende, damit der Server sie mitbekommt */
} record_t;

#define WIND_DIR_INVALID 0xFFFF

/* Soviele Datensaetze gehen hoechstens in einen Stapel. Mehr wuerde die
 * JSON-Nachricht ueber die Groesse treiben, die Mosquitto ohne Nachfrage
 * annimmt. */
#define BATCH_MAX 40

static rb_t s_buffer;
static sensor_registry_t s_sensors;
static power_mode_t s_mode = POWER_MODE_NORMAL;

/* --- Sensoren erkennen und Zustaende bestimmen --- */

static void sensoren_erkennen(void)
{
    sensor_registry_init(&s_sensors);

    /* Reihenfolge ist wichtig: erst die gespeicherten Zustaende, dann der
     * Bus-Scan. So ueberschreibt eine bewusst getroffene Einstellung nicht die
     * Autoerkennung -- und ein Neustart nimmt sie nicht zurueck. */
    sensor_store_load(&s_sensors);

    for (int i = 0; i < SENSOR_COUNT; ++i) {
        const uint8_t adresse = sensor_i2c_address((sensor_id_t)i);
        if (adresse == 0) {
            continue;  /* haengt nicht am Bus: Reed-Kontakte, Windfahne */
        }
        const bool da = i2c_bus_probe(adresse);
        sensor_registry_set_detected(&s_sensors, (sensor_id_t)i, da);
        ESP_LOGI(TAG, "%-12s 0x%02X %s", sensor_key((sensor_id_t)i), adresse,
                 da ? "gefunden" : "nicht da");
    }

    ESP_LOGI(TAG, "%d produktiv, %d im Test, %d aus",
             sensor_registry_count(&s_sensors, SENSOR_PRODUCTIVE),
             sensor_registry_count(&s_sensors, SENSOR_TEST),
             sensor_registry_count(&s_sensors, SENSOR_INACTIVE));
}

/* --- Messen --- */

static float s_gust_fenster;  /* hoechste Windgeschwindigkeit seit dem Senden */

static void messen(record_t *out, float seit_letzter_messung_s)
{
    memset(out, 0, sizeof(*out));
    out->unix_time = (uint32_t)timesync_now();
    out->sensor_states = sensor_registry_pack(&s_sensors);
    out->temperature_c = NAN;
    out->humidity_pct = NAN;
    out->pressure_hpa = NAN;
    out->wind_dir_deg = WIND_DIR_INVALID;

    if (sensor_registry_should_read(&s_sensors, SENSOR_BME280) && bme280_present()) {
        bme280_reading_t werte;
        if (bme280_read(&werte) == ESP_OK) {
            out->temperature_c = werte.temperature_c;
            out->humidity_pct = werte.humidity_pct;
            out->pressure_hpa = werte.pressure_hpa;
        }
    }

    if (sensor_registry_should_read(&s_sensors, SENSOR_WIND_SPEED)) {
        const uint32_t impulse = pulse_input_take(PULSE_WIND);
        out->wind_speed_ms = pulse_wind_speed_ms(impulse, seit_letzter_messung_s,
                                                 WIND_MS_PER_HZ_DEFAULT);
        if (out->wind_speed_ms > s_gust_fenster) {
            s_gust_fenster = out->wind_speed_ms;
        }
        out->wind_gust_ms = s_gust_fenster;
    }

    if (sensor_registry_should_read(&s_sensors, SENSOR_RAIN_GAUGE)) {
        out->rain_mm = pulse_rain_mm(pulse_input_take(PULSE_RAIN),
                                     RAIN_MM_PER_TIP_DEFAULT);
    }
}

/* --- Senden --- */

/* Ein Messwert, der NaN ist, wird als null geschrieben. Der Server unterscheidet
 * daran "nicht gemessen" von "gemessen und null" -- bei Niederschlag ist das der
 * Unterschied zwischen einer Luecke und trockenem Wetter. */
static int zahl_oder_null(char *buf, size_t len, const char *name, float wert)
{
    if (isnan(wert)) {
        return snprintf(buf, len, ",\"%s\":null", name);
    }
    return snprintf(buf, len, ",\"%s\":%.2f", name, (double)wert);
}

static int stapel_bauen(char *buf, size_t len, const rb_record_t *records,
                        uint32_t count, float akku, int rssi)
{
    char sensoren[320];
    if (sensor_registry_to_json(&s_sensors, sensoren, sizeof(sensoren)) < 0) {
        return -1;
    }

    int n = snprintf(buf, len,
                     "{\"station\":\"%s\",\"fw\":\"%s\",\"mode\":\"%s\","
                     "\"battery_v\":%.2f,\"rssi\":%d,\"sensors\":{%s},\"records\":[",
                     STATION_KEY, "0.1.0", power_mode_name(s_mode), (double)akku,
                     rssi, sensoren);
    if (n < 0 || (size_t)n >= len) {
        return -1;
    }

    for (uint32_t i = 0; i < count; ++i) {
        const record_t *r = (const record_t *)records[i].payload;
        int m = snprintf(buf + n, len - n, "%s{\"t\":%lu,\"seq\":%lu", i ? "," : "",
                         (unsigned long)r->unix_time, (unsigned long)records[i].seq);
        if (m < 0 || (size_t)(n + m) >= len) {
            return -1;
        }
        n += m;

        n += zahl_oder_null(buf + n, len - n, "temp", r->temperature_c);
        n += zahl_oder_null(buf + n, len - n, "hum", r->humidity_pct);
        n += zahl_oder_null(buf + n, len - n, "press", r->pressure_hpa);
        n += zahl_oder_null(buf + n, len - n, "wind", r->wind_speed_ms);
        n += zahl_oder_null(buf + n, len - n, "gust", r->wind_gust_ms);
        n += zahl_oder_null(buf + n, len - n, "rain", r->rain_mm);
        if (r->wind_dir_deg != WIND_DIR_INVALID) {
            n += snprintf(buf + n, len - n, ",\"dir\":%u", r->wind_dir_deg);
        }
        n += snprintf(buf + n, len - n, "}");
        if ((size_t)n >= len) {
            return -1;
        }
    }

    n += snprintf(buf + n, len - n, "]}");
    return (size_t)n < len ? n : -1;
}

static void senden(float akku)
{
    if (rb_pending(&s_buffer) == 0) {
        return;
    }

    if (wifi_connect(15000) != ESP_OK) {
        return;
    }
    if (mqtt_link_start(10000) != ESP_OK) {
        wifi_disconnect();
        return;
    }

    /* Solange etwas im Puffer liegt, stapelweise senden -- so holt die Station
     * nach einem Ausfall in einem Rutsch auf, statt bei jedem Takt nur einen
     * Stapel loszuwerden. */
    static rb_record_t records[BATCH_MAX];
    static char nutzlast[6144];

    while (rb_pending(&s_buffer) > 0) {
        uint32_t anzahl = 0;
        if (rb_peek(&s_buffer, records, BATCH_MAX, &anzahl) != RB_OK || anzahl == 0) {
            break;
        }

        const int len = stapel_bauen(nutzlast, sizeof(nutzlast), records, anzahl,
                                     akku, wifi_rssi());
        if (len < 0) {
            ESP_LOGE(TAG, "Stapel passt nicht in den Puffer");
            break;
        }

        if (mqtt_link_publish_batch(nutzlast, len, 10000) != ESP_OK) {
            /* Ohne Bestaetigung nichts freigeben. Beim naechsten Mal erneut. */
            break;
        }

        rb_ack_through(&s_buffer, records[anzahl - 1].seq);
        ESP_LOGI(TAG, "%lu Datensaetze zugestellt, %lu bleiben",
                 (unsigned long)anzahl, (unsigned long)rb_pending(&s_buffer));
    }

    /* Die Boee gilt je Sendezeitraum. */
    s_gust_fenster = 0.0f;

    mqtt_link_stop();
    wifi_disconnect();
}

/* --- Befehle vom Server --- */

static void befehl_empfangen(const char *json, int len)
{
    /* Bewusst kein JSON-Parser: die Nachricht ist winzig und hat eine feste
     * Form. Ein Parser waere hier mehr Code und mehr Angriffsflaeche als Nutzen.
     * Gesucht wird schlicht nach "sensorname":"zustand". */
    bool geaendert = false;

    for (int i = 0; i < SENSOR_COUNT; ++i) {
        char muster[48];
        const int m = snprintf(muster, sizeof(muster), "\"%s\":\"",
                               sensor_key((sensor_id_t)i));
        if (m < 0) {
            continue;
        }
        const char *p = memmem(json, len, muster, m);
        if (p == NULL) {
            continue;
        }
        p += m;

        sensor_state_t zustand;
        char wort[16] = {0};
        for (size_t k = 0; k < sizeof(wort) - 1 && p + k < json + len; ++k) {
            if (p[k] == '"') {
                break;
            }
            wort[k] = p[k];
        }
        if (!sensor_state_from_name(wort, &zustand)) {
            ESP_LOGW(TAG, "unbekannter Zustand %s fuer %s", wort,
                     sensor_key((sensor_id_t)i));
            continue;
        }
        if (sensor_registry_set_state(&s_sensors, (sensor_id_t)i, zustand)) {
            ESP_LOGI(TAG, "%s -> %s", sensor_key((sensor_id_t)i), wort);
            geaendert = true;
        } else {
            ESP_LOGW(TAG, "%s laesst sich nicht auf %s setzen (nicht erkannt?)",
                     sensor_key((sensor_id_t)i), wort);
        }
    }

    if (geaendert) {
        sensor_store_save(&s_sensors);
    }
}

/* --- Start --- */

void app_main(void)
{
    ESP_LOGI(TAG, "Wetterstation %s startet", STATION_KEY);

    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    ESP_ERROR_CHECK(i2c_bus_init(PIN_I2C_SDA, PIN_I2C_SCL));
    bme280_init();  /* darf fehlschlagen -- dann fehlt eben der Sensor */
    sensoren_erkennen();

    rb_flash_t flash;
    ESP_ERROR_CHECK(flash_backend_init(&flash));
    if (rb_init(&s_buffer, &flash, sizeof(record_t)) != RB_OK) {
        ESP_LOGE(TAG, "Ringpuffer laesst sich nicht einrichten");
        esp_restart();
    }
    ESP_LOGI(TAG, "Ringpuffer: %lu unbestaetigte Datensaetze aus dem letzten Lauf",
             (unsigned long)rb_pending(&s_buffer));

    pulse_input_init(PULSE_WIND, PIN_WIND_PULSE, PULSE_DEBOUNCE_WIND_US);
    pulse_input_init(PULSE_RAIN, PIN_RAIN_PULSE, PULSE_DEBOUNCE_RAIN_US);

    ESP_ERROR_CHECK(power_init(PIN_BATTERY_ADC, BATTERY_DIVIDER));
    power_enable_light_sleep(80, 10);

    ESP_ERROR_CHECK(wifi_init(WIFI_SSID, WIFI_PASSWORD));

    const mqtt_link_config_t mcfg = {
        .host = MQTT_HOST,
        .port = MQTT_PORT,
        .username = MQTT_USER,
        .password = MQTT_PASSWORD,
        .station_key = STATION_KEY,
    };
    ESP_ERROR_CHECK(mqtt_link_init(&mcfg, befehl_empfangen));

    /* Ohne richtige Zeit ist ein gepufferter Datensatz wertlos -- also einmal
     * warten, bevor die erste Messung in den Puffer geht. */
    if (wifi_connect(20000) == ESP_OK) {
        timesync_start(SNTP_SERVER);
        timesync_wait(15000);
        wifi_disconnect();
    }
    if (!timesync_valid()) {
        ESP_LOGW(TAG, "keine gueltige Zeit -- Messung wartet auf den Abgleich");
    }

    int64_t letzte_messung_us = esp_timer_get_time();
    int64_t letztes_senden_us = letzte_messung_us;

    while (true) {
        const power_profile_t profil = power_profile(s_mode);
        const int64_t jetzt = esp_timer_get_time();
        const float seit_messung = (float)(jetzt - letzte_messung_us) / 1e6f;

        if (seit_messung >= (float)profil.measure_seconds) {
            letzte_messung_us = jetzt;

            if (timesync_valid()) {
                record_t datensatz;
                messen(&datensatz, seit_messung);
                if (rb_append(&s_buffer, &datensatz, sizeof(datensatz), NULL) != RB_OK) {
                    ESP_LOGE(TAG, "Datensatz liess sich nicht ablegen");
                }
            }
        }

        if ((float)(jetzt - letztes_senden_us) / 1e6f >= (float)profil.publish_seconds) {
            letztes_senden_us = jetzt;

            const float akku = power_battery_volts();
            const power_mode_t neu = power_decide(s_mode, akku);
            if (neu != s_mode) {
                ESP_LOGW(TAG, "Betriebsart %s -> %s bei %.2f V",
                         power_mode_name(s_mode), power_mode_name(neu), (double)akku);
                s_mode = neu;
            }
            senden(akku);
        }

        /* Kurz schlafen. Mit aktiviertem Light-Sleep faellt der Kern dabei von
         * selbst in den Schlaf und wird vom Timer oder von einem Reed-Impuls
         * geweckt. */
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
