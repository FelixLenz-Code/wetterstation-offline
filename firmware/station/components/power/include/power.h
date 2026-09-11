/* Energieverwaltung der Aussenstation.
 *
 * Die Station haengt an einem 10-W-Panel mit Akku. Im Sommer ist das reichlich,
 * im Dezember bei einer Woche Hochnebel wird es knapp -- und genau dann darf sie
 * nicht ausfallen, weil Frost und Nebel die Lagen sind, in denen die eigene
 * Vorhersage am meisten wert ist.
 *
 * Deshalb drei Betriebsarten statt einer. Sie werden nach Akkuspannung
 * umgeschaltet, mit Hysterese: ohne die schaltet die Station im Grenzbereich
 * staendig hin und her, und jeder Wechsel kostet mehr, als er spart.
 */
#ifndef POWER_H
#define POWER_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

/* Betriebsarten und ihre Entscheidung stehen in power_policy.h -- dort ohne
 * ESP-Abhaengigkeiten und damit auf dem Rechner pruefbar. */
#include "power_policy.h"

/* Richtet die Akkumessung ein. Der Teiler halbiert die Spannung, damit sie in
 * den Messbereich des ADC passt. */
esp_err_t power_init(int adc_gpio, float divider_ratio);

/* Misst die Akkuspannung. Gibt einen negativen Wert zurueck, wenn keine Messung
 * moeglich ist -- dann gilt weiter die zuletzt gewaehlte Betriebsart. */
float power_battery_volts(void);

/* Entscheidet die Betriebsart. current ist die bisherige -- fuer die Hysterese. */
power_mode_t power_decide(power_mode_t current, float volts);

/* Schaltet die dynamische Taktanpassung samt automatischem Light-Sleep ein. */
esp_err_t power_enable_light_sleep(int max_mhz, int min_mhz);

#endif /* POWER_H */
