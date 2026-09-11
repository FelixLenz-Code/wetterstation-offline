/* Umrechnung von Reed-Impulsen in Messwerte.
 *
 * Bewusst getrennt von der Interrupt-Logik in pulse_input.c: hier stehen nur
 * Zahlen und Formeln, und die lassen sich auf dem Rechner pruefen. Die
 * Konstanten sind die fehleranfaelligste Stelle der ganzen Station -- ein
 * falscher Faktor faellt niemandem auf, weil das Ergebnis plausibel aussieht.
 */
#ifndef PULSE_CONVERT_H
#define PULSE_CONVERT_H

#include <stdint.h>

/* Niederschlag je Kippung der Wippe in Millimetern.
 *
 * 0,2794 mm sind genau elf Tausendstel Zoll (0,011 x 25,4). Die verbreiteten
 * Wippen von Fine Offset und Argent stammen aus dem US-Markt und sind darauf
 * ausgelegt; die oft genannten "0,2 mm" sind eine gerundete Faustzahl aus
 * Datenblaettern anderer Hersteller. Wer sie fuer diese Wippe annimmt, misst rund
 * 28 Prozent zu wenig Regen -- und weil der Niederschlag zugleich Messgroesse und
 * Trainingsziel ist, lernt das Modell den Fehler mit.
 *
 * Die eigene Wippe sollte einmal ausgelitert werden: bekannte Wassermenge
 * langsam eingiessen, Kippungen zaehlen. Siehe hardware/regenmesser.md. */
#define RAIN_MM_PER_TIP_DEFAULT 0.2794f

/* Windgeschwindigkeit je Hertz in m/s.
 *
 * Der WH-SP-WS01 liefert laut Hersteller 2,4 km/h bei einer Umdrehung je
 * Sekunde. 2,4 km/h sind 0,6667 m/s. */
#define WIND_MS_PER_HZ_DEFAULT 0.66667f

float pulse_wind_speed_ms(uint32_t pulses, float seconds, float ms_per_hz);
float pulse_rain_mm(uint32_t pulses, float mm_per_tip);

/* Boee: hoechste Windgeschwindigkeit in einem kurzen Fenster.
 *
 * Die WMO definiert eine Boee als Drei-Sekunden-Mittel. Ein kuerzeres Fenster
 * misst einzelne Umdrehungen und meldet Unsinn, ein laengeres glaettet die Boee
 * weg -- und die Boee ist genau das, wovor eine Sturmwarnung warnen soll. */
#define GUST_WINDOW_SECONDS 3.0f

float pulse_gust_ms(const uint32_t *window_pulses, int count, float window_seconds,
                    float ms_per_hz);

#endif /* PULSE_CONVERT_H */
