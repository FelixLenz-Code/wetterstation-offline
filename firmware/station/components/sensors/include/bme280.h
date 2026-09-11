/* BME280: Luftdruck, Temperatur und Feuchte.
 *
 * Der einzige Pflichtsensor der Station. Ohne Druck gibt es keine Drucktendenz,
 * und ohne die keine Vorhersage, die diesen Namen verdient.
 *
 * Die Rechnung steckt in bme280_compensate.c und ist ohne Hardware testbar; hier
 * steht nur der Busverkehr.
 */
#ifndef BME280_H
#define BME280_H

#include <stdbool.h>

#include "esp_err.h"

/* Der Chip sitzt je nach Modul auf 0x76 oder 0x77 -- SDO auf Masse oder auf
 * Versorgung. Billige Module verdrahten das unterschiedlich, deshalb werden
 * beide Adressen probiert. */
#define BME280_ADDR_PRIMARY 0x76
#define BME280_ADDR_SECONDARY 0x77

typedef struct {
    float temperature_c;
    float humidity_pct;
    float pressure_hpa;
} bme280_reading_t;

/* Sucht den Chip auf beiden Adressen, prueft die Kennung und liest die
 * Kalibrierdaten. Muss einmal nach i2c_bus_init laufen. */
esp_err_t bme280_init(void);

bool bme280_present(void);

/* Liest einen Messwert. Loest selbst eine Einzelmessung aus und wartet, bis sie
 * fertig ist -- der Dauermessmodus waere bequemer, zieht aber staendig Strom. */
esp_err_t bme280_read(bme280_reading_t *out);

#endif /* BME280_H */
