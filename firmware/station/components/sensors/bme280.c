#include "bme280.h"

#include "bme280_compensate.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "i2c_bus.h"

static const char *TAG = "bme280";

#define REG_ID 0xD0
#define REG_RESET 0xE0
#define REG_CTRL_HUM 0xF2
#define REG_STATUS 0xF3
#define REG_CTRL_MEAS 0xF4
#define REG_CONFIG 0xF5
#define REG_DATA 0xF7
#define REG_CALIB_88 0x88
#define REG_CALIB_E1 0xE1

/* Der BME280 meldet 0x60. Ein BMP280 -- der keine Feuchte kann -- meldet 0x58.
 * Genau diese Verwechslung war beim Bestellen schon einmal Thema, deshalb wird
 * sie hier ausdruecklich erkannt und gemeldet. */
#define CHIP_ID_BME280 0x60
#define CHIP_ID_BMP280 0x58

/* Ueberabtastung: zweifach reicht fuer Temperatur und Feuchte, der Druck bekommt
 * sechzehnfach. Er ist die wichtigste Groesse der Vorhersage, und die Tendenz
 * ueber drei Stunden bewegt sich in Zehntel-hPa -- da lohnt sich das Rauschen zu
 * druecken, auch wenn die Messung dadurch laenger dauert. */
#define OSRS_T 2  /* x2 */
#define OSRS_P 5  /* x16 */
#define OSRS_H 2  /* x2 */
#define MODE_FORCED 1

static uint8_t s_address;
static bool s_present;
static bme280_calib_t s_calib;

static esp_err_t kalibrierung_lesen(void)
{
    uint8_t roh88[26];
    uint8_t rohe1[7];

    esp_err_t err = i2c_bus_read(s_address, REG_CALIB_88, roh88, sizeof(roh88));
    if (err != ESP_OK) {
        return err;
    }
    err = i2c_bus_read(s_address, REG_CALIB_E1, rohe1, sizeof(rohe1));
    if (err != ESP_OK) {
        return err;
    }

    if (!bme280_parse_calibration(roh88, rohe1, &s_calib)) {
        ESP_LOGE(TAG, "Kalibrierdaten unbrauchbar -- falsche Adresse?");
        return ESP_ERR_INVALID_RESPONSE;
    }
    return ESP_OK;
}

esp_err_t bme280_init(void)
{
    s_present = false;

    const uint8_t adressen[] = {BME280_ADDR_PRIMARY, BME280_ADDR_SECONDARY};
    for (size_t i = 0; i < sizeof(adressen); ++i) {
        if (!i2c_bus_probe(adressen[i])) {
            continue;
        }
        uint8_t kennung = 0;
        if (i2c_bus_read(adressen[i], REG_ID, &kennung, 1) != ESP_OK) {
            continue;
        }
        if (kennung == CHIP_ID_BMP280) {
            ESP_LOGE(TAG,
                     "Auf 0x%02X sitzt ein BMP280 (Kennung 0x58), kein BME280. "
                     "Der kann keine Feuchte -- ohne sie fehlt der Taupunkt und "
                     "damit ein Grossteil der Nebel- und Frostvorhersage.",
                     adressen[i]);
            continue;
        }
        if (kennung != CHIP_ID_BME280) {
            ESP_LOGW(TAG, "Auf 0x%02X antwortet Kennung 0x%02X, erwartet 0x60",
                     adressen[i], kennung);
            continue;
        }
        s_address = adressen[i];
        s_present = true;
        break;
    }

    if (!s_present) {
        ESP_LOGW(TAG, "Kein BME280 gefunden (weder 0x76 noch 0x77)");
        return ESP_ERR_NOT_FOUND;
    }

    esp_err_t err = kalibrierung_lesen();
    if (err != ESP_OK) {
        s_present = false;
        return err;
    }

    /* Filter aus: er glaettet ueber mehrere Messungen und ist fuer den
     * Dauermessmodus gedacht. Bei Einzelmessungen alle zehn Sekunden wuerde er
     * nur alte Werte mitschleppen. */
    err = i2c_bus_write(s_address, REG_CONFIG, 0x00);
    if (err == ESP_OK) {
        /* ctrl_hum wirkt erst, wenn danach ctrl_meas geschrieben wird -- eine
         * Eigenheit des Chips, die man leicht uebersieht. */
        err = i2c_bus_write(s_address, REG_CTRL_HUM, OSRS_H);
    }
    if (err != ESP_OK) {
        s_present = false;
        return err;
    }

    ESP_LOGI(TAG, "BME280 auf 0x%02X bereit", s_address);
    return ESP_OK;
}

bool bme280_present(void)
{
    return s_present;
}

esp_err_t bme280_read(bme280_reading_t *out)
{
    if (!s_present || out == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    const uint8_t ctrl = (uint8_t)((OSRS_T << 5) | (OSRS_P << 2) | MODE_FORCED);
    esp_err_t err = i2c_bus_write(s_address, REG_CTRL_MEAS, ctrl);
    if (err != ESP_OK) {
        return err;
    }

    /* Bei x2/x16/x2 braucht die Messung laut Datenblatt gut 40 ms. Statt blind
     * zu warten wird das Statusregister abgefragt -- so bleibt es richtig, wenn
     * jemand die Ueberabtastung aendert. */
    for (int versuch = 0; versuch < 20; ++versuch) {
        vTaskDelay(pdMS_TO_TICKS(10));
        uint8_t status = 0;
        if (i2c_bus_read(s_address, REG_STATUS, &status, 1) != ESP_OK) {
            return ESP_FAIL;
        }
        if ((status & 0x08) == 0) {
            break;
        }
    }

    uint8_t daten[8];
    err = i2c_bus_read(s_address, REG_DATA, daten, sizeof(daten));
    if (err != ESP_OK) {
        return err;
    }

    int32_t adc_P, adc_T, adc_H;
    bme280_parse_raw(daten, &adc_P, &adc_T, &adc_H);
    if (!bme280_raw_is_valid(adc_T, adc_P, adc_H)) {
        ESP_LOGW(TAG, "Wandlerwerte ungueltig -- Kanal abgeschaltet?");
        return ESP_ERR_INVALID_RESPONSE;
    }

    /* Reihenfolge ist Pflicht: Druck und Feuchte brauchen das t_fine aus der
     * Temperaturrechnung. */
    bme280_state_t zustand = {0};
    out->temperature_c = (float)bme280_compensate_temperature(&s_calib, &zustand, adc_T);
    out->pressure_hpa =
        (float)(bme280_compensate_pressure(&s_calib, &zustand, adc_P) / 100.0);
    out->humidity_pct = (float)bme280_compensate_humidity(&s_calib, &zustand, adc_H);
    return ESP_OK;
}
