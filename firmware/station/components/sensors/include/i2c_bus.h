/* Gemeinsamer I2C-Bus fuer alle Sensoren.
 *
 * Alle Sensoren der Station haengen am selben Bus. Ein eigener Bus je Sensor
 * waere Verschwendung an Pins und Strom; ein gemeinsamer verlangt dafuer, dass
 * ein haengender Sensor nicht die uebrigen mitreisst -- deshalb hat jeder
 * Zugriff eine Zeitgrenze, und ein Fehlschlag meldet sich, statt zu blockieren.
 */
#ifndef I2C_BUS_H
#define I2C_BUS_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

/* Standardpins. Am ESP32 sind GPIO 21 und 22 die ueblichen I2C-Pins und auf den
 * meisten Entwicklungsboards bereits so beschriftet. */
#define I2C_BUS_SDA_DEFAULT 21
#define I2C_BUS_SCL_DEFAULT 22

/* 100 kHz statt 400: die Sensoren haengen an mehreren Metern Kabel im Mast, und
 * ein langsamer Bus ist dort deutlich weniger stoeranfaellig. Schneller waere
 * ohnehin sinnlos -- gelesen wird alle zehn Sekunden. */
#define I2C_BUS_FREQ_HZ 100000

esp_err_t i2c_bus_init(int sda, int scl);
void i2c_bus_deinit(void);

/* Meldet, ob ein Geraet auf der Adresse antwortet. Grundlage der Autoerkennung
 * beim Start. */
bool i2c_bus_probe(uint8_t address);

/* Registerzugriffe. Alle mit Zeitgrenze; ein stummer Sensor blockiert nichts. */
esp_err_t i2c_bus_read(uint8_t address, uint8_t reg, uint8_t *out, size_t len);
esp_err_t i2c_bus_write(uint8_t address, uint8_t reg, uint8_t value);

#endif /* I2C_BUS_H */
