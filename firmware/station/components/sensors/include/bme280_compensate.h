/* Kompensationsrechnung des BME280.
 *
 * Der Sensor liefert keine Messwerte, sondern rohe Wandlerwerte. Erst zusammen mit
 * den im Chip gebrannten Kalibrierkonstanten und den Formeln aus dem Datenblatt
 * wird daraus Temperatur, Druck und Feuchte. Die Formeln sind lang, voll mit
 * Schiebeoperationen und Zahlenkonstanten -- genau die Sorte Code, in der ein
 * Tippfehler plausible, aber falsche Werte liefert.
 *
 * Deshalb stehen hier *zwei* Fassungen: die Ganzzahlvariante und die
 * Fliesskommavariante, beide aus dem Datenblatt. Der Test prüft, dass sie
 * übereinstimmen. Ein Vertipper in einer der beiden fällt damit auf, ohne dass es
 * Referenzwerte von aussen bräuchte -- zwei unabhängig abgeschriebene Formeln
 * irren sich nicht auf dieselbe Weise.
 *
 * Frei von ESP-IDF-Abhängigkeiten, damit die Rechnung auf dem Rechner laufen kann.
 */
#ifndef BME280_COMPENSATE_H
#define BME280_COMPENSATE_H

#include <stdbool.h>
#include <stdint.h>

/* Kalibrierkonstanten, wie sie aus den Registern 0x88..0xA1 und 0xE1..0xE7
 * gelesen werden. Die Namen folgen dem Datenblatt, damit man beim Vergleichen
 * nicht übersetzen muss. */
typedef struct {
    uint16_t dig_T1;
    int16_t dig_T2;
    int16_t dig_T3;

    uint16_t dig_P1;
    int16_t dig_P2;
    int16_t dig_P3;
    int16_t dig_P4;
    int16_t dig_P5;
    int16_t dig_P6;
    int16_t dig_P7;
    int16_t dig_P8;
    int16_t dig_P9;

    uint8_t dig_H1;
    int16_t dig_H2;
    uint8_t dig_H3;
    int16_t dig_H4;
    int16_t dig_H5;
    int8_t dig_H6;
} bme280_calib_t;

/* Zwischenwert aus der Temperaturrechnung. Druck und Feuchte hängen davon ab --
 * deshalb muss die Temperatur immer zuerst gerechnet werden. */
typedef struct {
    int32_t t_fine;
    double t_fine_d;
} bme280_state_t;

/* --- Ganzzahlfassung (Datenblatt, Anhang A) --- */

/* Temperatur in Hundertstel Grad Celsius. Setzt state->t_fine. */
int32_t bme280_compensate_temperature_int(const bme280_calib_t *c,
                                          bme280_state_t *state, int32_t adc_T);

/* Druck in Pa (Q24.8, also Pa mal 256). Braucht ein zuvor gesetztes t_fine. */
uint32_t bme280_compensate_pressure_int(const bme280_calib_t *c,
                                        const bme280_state_t *state, int32_t adc_P);

/* Relative Feuchte in Q22.10 (also Prozent mal 1024). */
uint32_t bme280_compensate_humidity_int(const bme280_calib_t *c,
                                        const bme280_state_t *state, int32_t adc_H);

/* --- Fliesskommafassung (Datenblatt, Anhang B) --- */

double bme280_compensate_temperature(const bme280_calib_t *c,
                                     bme280_state_t *state, int32_t adc_T);
double bme280_compensate_pressure(const bme280_calib_t *c,
                                  const bme280_state_t *state, int32_t adc_P);
double bme280_compensate_humidity(const bme280_calib_t *c,
                                  const bme280_state_t *state, int32_t adc_H);

/* Zerlegt die 26 Bytes aus 0x88..0xA1 und 0xE1..0xE7 in die Konstanten.
 * calib_88 muss 26 Bytes lang sein, calib_e1 sieben. */
bool bme280_parse_calibration(const uint8_t *calib_88, const uint8_t *calib_e1,
                              bme280_calib_t *out);

/* Wandelt die drei rohen 20-Bit- bzw. 16-Bit-Werte aus einem Burst-Read
 * (Register 0xF7..0xFE, acht Bytes) in die Wandlerwerte. */
void bme280_parse_raw(const uint8_t *data, int32_t *adc_P, int32_t *adc_T,
                      int32_t *adc_H);

/* Ein Wandlerwert von 0x80000 (Druck/Temperatur) bzw. 0x8000 (Feuchte) heisst:
 * der Kanal ist abgeschaltet oder hat noch nicht gemessen. */
#define BME280_ADC_DISABLED_TP 0x80000
#define BME280_ADC_DISABLED_H 0x8000

bool bme280_raw_is_valid(int32_t adc_T, int32_t adc_P, int32_t adc_H);

#endif /* BME280_COMPENSATE_H */
