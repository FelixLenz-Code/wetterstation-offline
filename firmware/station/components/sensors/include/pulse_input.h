/* Impulszaehlung fuer Anemometer und Regenwippe.
 *
 * Beides sind Reed-Kontakte: der Schalenstern schliesst einmal je Umdrehung, die
 * Wippe einmal je 0,2 mm Niederschlag. Gezaehlt wird per GPIO-Interrupt, nicht
 * ueber den PCNT-Zaehler des ESP32 -- der haelt den Light-Sleep nicht durch,
 * weil sein Takt dabei abgeschaltet wird. Der Interrupt weckt den Kern dagegen
 * kurz auf, zaehlt und laesst ihn weiterschlafen.
 *
 * Genau darum laeuft die Station im Dauerbetrieb mit Light-Sleep und nicht im
 * Tiefschlaf: ein klassischer ESP32 hat keinen in C programmierbaren ULP-Kern,
 * und im Tiefschlaf ginge jeder Impuls verloren. Bei Sturm sind das die Boeen,
 * bei Starkregen die halbe Regenmenge.
 *
 * Prellen: mechanische Reed-Kontakte schliessen nicht sauber. Ohne Entprellung
 * zaehlt ein einzelner Windstoss als mehrere Umdrehungen, und die Station meldet
 * Orkan bei Windstille.
 */
#ifndef PULSE_INPUT_H
#define PULSE_INPUT_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

typedef enum {
    PULSE_WIND = 0,
    PULSE_RAIN,
    PULSE_CHANNELS,
} pulse_channel_t;

/* Kuerzeste Zeit zwischen zwei gueltigen Impulsen.
 *
 * Wind: der WH-SP-WS01 liefert 1 Hz bei 2,4 km/h. Ein Orkan mit 200 km/h waere
 * rund 83 Hz, also 12 ms Abstand. 5 ms lassen dafuer reichlich Luft und fangen
 * das Prellen trotzdem ab.
 *
 * Regen: eine Wippe kippt selbst bei Wolkenbruch hoechstens ein paar Mal je
 * Sekunde. 150 ms sind grosszuegig entprellt und verlieren nichts. */
#define PULSE_DEBOUNCE_WIND_US 5000
#define PULSE_DEBOUNCE_RAIN_US 150000

/* Richtet einen Kanal ein. Der Pin bekommt einen internen Pull-up; der
 * Reed-Kontakt zieht gegen Masse. */
esp_err_t pulse_input_init(pulse_channel_t channel, int gpio, uint32_t debounce_us);

/* Liest den Zaehler und setzt ihn zurueck -- in einem Schritt, damit zwischen
 * Lesen und Zuruecksetzen kein Impuls verlorengeht. */
uint32_t pulse_input_take(pulse_channel_t channel);

/* Liest, ohne zurueckzusetzen. */
uint32_t pulse_input_peek(pulse_channel_t channel);

/* Wieviele Impulse als Prellen verworfen wurden. Gehoert ins Log: ein stark
 * steigender Wert heisst, der Kontakt ist verschlissen oder das Kabel stoert. */
uint32_t pulse_input_rejected(pulse_channel_t channel);

/* Die Umrechnung in Messwerte steht in pulse_convert.h -- dort ohne
 * ESP-Abhaengigkeiten und damit auf dem Rechner pruefbar. */
#include "pulse_convert.h"

#endif /* PULSE_INPUT_H */
