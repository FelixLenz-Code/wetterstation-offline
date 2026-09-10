/* Windfahne des WH-SP-WS01-Sets auswerten.
 *
 * Die Fahne ist kein Drehgeber, sondern ein Ring aus acht Reed-Kontakten mit je
 * einem Widerstand. Steht die Fahne genau auf einem Kontakt, schliesst einer;
 * steht sie dazwischen, schliessen zwei benachbarte gleichzeitig und die
 * Parallelschaltung ergibt einen weiteren Wert. So entstehen 16 unterscheidbare
 * Widerstaende fuer 16 Richtungen im Abstand von 22,5 Grad.
 *
 * Die Werte liegen absichtlich nicht monoton zur Richtung -- man kann also nicht
 * einfach interpolieren, sondern muss den naechstliegenden Tabellenwert suchen.
 */
#ifndef WIND_VANE_H
#define WIND_VANE_H

#include <stdbool.h>
#include <stdint.h>

#define WIND_VANE_POSITIONS 16

/* Ungueltige Richtung -- Fahne nicht angeschlossen oder Messwert unplausibel. */
#define WIND_VANE_INVALID (-1.0f)

/* Beschaltung der Fahne als Spannungsteiler.
 *
 * Uebliche Beschaltung: Versorgung -> Fahne -> Messpunkt -> series_ohms -> Masse.
 * Der ADC misst am Messpunkt.
 */
typedef struct {
    float supply_volts;  /* Speisespannung des Teilers, typisch 3.3 */
    float series_ohms;   /* Festwiderstand gegen Masse, typisch 10000 */
    float tolerance;     /* erlaubte relative Abweichung, siehe WIND_VANE_MAX_TOLERANCE */
} wind_vane_config_t;

/* Hoechste Toleranz, bei der noch alle 16 Richtungen eindeutig bleiben.
 *
 * Die Tabelle ist an einer Stelle sehr eng: 891 Ohm (67,5 Grad) und 1000 Ohm
 * (90 Grad) liegen nur 12 % auseinander. Die Grenze, an der ein Messwert von der
 * einen zur anderen Richtung kippt, liegt bei 942 Ohm -- also 5,76 % neben dem
 * Sollwert. Wer die Toleranz hoeher setzt, bekommt keine Fehlermeldung, sondern
 * still eine um 22,5 Grad verdrehte Windrichtung.
 *
 * Praktische Folge: die ueblichen 5-%-Widerstaende in der Fahne reichen dafuer
 * gerade so nicht sicher aus. Deshalb sollte die Fahne einmal ausgemessen und die
 * Tabelle per wind_vane_set_table() ersetzt werden -- siehe hardware/windfahne.md.
 */
#define WIND_VANE_MAX_TOLERANCE 0.055f

#define WIND_VANE_CONFIG_DEFAULT                                     \
    ((wind_vane_config_t){.supply_volts = 3.3f,                      \
                          .series_ohms = 10000.0f,                   \
                          .tolerance = WIND_VANE_MAX_TOLERANCE})

/* Widerstand der Fahne aus der gemessenen Teilerspannung.
 * Liefert einen negativen Wert, wenn die Spannung ausserhalb des Teilerbereichs liegt. */
float wind_vane_resistance(const wind_vane_config_t *cfg, float measured_volts);

/* Richtung in Grad (0 = Nord, im Uhrzeigersinn) aus dem Fahnenwiderstand.
 * Liefert WIND_VANE_INVALID, wenn kein Tabellenwert innerhalb der Toleranz passt. */
float wind_vane_direction_from_ohms(const wind_vane_config_t *cfg, float ohms);

/* Bequemer Weg: direkt von der gemessenen Spannung zur Richtung. */
float wind_vane_direction(const wind_vane_config_t *cfg, float measured_volts);

/* Tabelleneintrag lesen -- fuer Tests und zum Kalibrieren. */
bool wind_vane_entry(int index, float *ohms_out, float *degrees_out);

/* Ersetzt die Widerstandstabelle durch ausgemessene Werte der eigenen Fahne.
 *
 * ohms muss WIND_VANE_POSITIONS Werte enthalten, in derselben Reihenfolge wie die
 * Vorgabetabelle (also nach Richtung: 0 Grad, 22,5 Grad, ... 337,5 Grad).
 * Liefert false, wenn ein Wert unplausibel ist oder zwei Werte so nah beieinander
 * liegen, dass sie sich bei der eingestellten Toleranz nicht trennen lassen. */
bool wind_vane_set_table(const float *ohms, float tolerance);

/* Setzt die Vorgabetabelle wieder ein. */
void wind_vane_reset_table(void);

/* Kleinster relativer Abstand zweier Tabellenwerte -- die hoechste noch sichere
 * Toleranz. Zum Pruefen einer selbst ausgemessenen Tabelle. */
float wind_vane_min_separation(void);

#endif /* WIND_VANE_H */
