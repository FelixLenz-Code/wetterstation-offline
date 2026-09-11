/* Entscheidung ueber die Betriebsart -- ohne Hardware, damit pruefbar.
 *
 * Die Station haengt an einem 10-W-Panel mit Akku. Im Sommer ist das reichlich,
 * im Dezember bei einer Woche Hochnebel wird es knapp -- und genau dann darf sie
 * nicht ausfallen, weil Frost und Nebel die Lagen sind, in denen die eigene
 * Vorhersage am meisten wert ist.
 *
 * Die Hysterese ist der Grund, warum diese Logik eine eigene Datei bekommt: ohne
 * sie schaltet die Station im Grenzbereich staendig hin und her. Bei
 * Sonnenaufgang steigt die Spannung kurz an, sie schaltet hoch, die groessere
 * Last zieht sie wieder herunter -- und das im Minutentakt. Jeder Wechsel kostet
 * dabei mehr, als er spart.
 */
#ifndef POWER_POLICY_H
#define POWER_POLICY_H

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    POWER_MODE_NORMAL = 0,  /* Light-Sleep-Dauerbetrieb, alle Sensoren */
    POWER_MODE_SAVING,      /* seltener messen, Zusatzsensoren zurueckgefahren */
    POWER_MODE_EMERGENCY,   /* Tiefschlaf, nur Druck und Temperatur */
    POWER_MODE_COUNT,
} power_mode_t;

/* Schwellen in Volt fuer eine Li-Ion-Zelle.
 *
 * 3,60 V heisst noch rund die Haelfte der Ladung, 3,40 V knapp ein Viertel.
 * Unter 3,20 V schaltet der Schutzschalter ohnehin ab -- so weit soll es nie
 * kommen. */
#define POWER_V_NORMAL 3.60f
#define POWER_V_SAVING 3.40f

/* Erst 0,08 V ueber der Schwelle wird wieder hochgeschaltet. */
#define POWER_HYSTERESIS_V 0.08f

typedef struct {
    uint32_t measure_seconds;
    uint32_t publish_seconds;
    bool extra_sensors;  /* MLX90614, BH1750, AS3935 */
    bool deep_sleep;
} power_profile_t;

const char *power_mode_name(power_mode_t mode);
power_profile_t power_profile(power_mode_t mode);

/* Entscheidet die Betriebsart. ``current`` ist die bisherige -- ohne sie gaebe
 * es keine Hysterese. Eine unbrauchbare Messung (Wert <= 0) laesst die bisherige
 * Betriebsart stehen: lieber weitermachen als wegen eines Messfehlers in den
 * Notbetrieb fallen. */
power_mode_t power_decide(power_mode_t current, float volts);

#endif /* POWER_POLICY_H */
