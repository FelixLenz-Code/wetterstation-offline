/* Zeitabgleich per SNTP.
 *
 * Ohne richtige Zeit ist ein gepufferter Datensatz wertlos: der Server ordnet
 * ihn falsch ein, und die Merkmalsberechnung rechnet mit Tendenzen ueber
 * Zeitraeume, die es nie gab. Deshalb wird vor der ersten Messung gewartet, bis
 * die Zeit steht.
 *
 * Als Quelle dient der eigene Server, nicht ein Zeitserver im Internet -- das
 * passt zum Offline-Anspruch und funktioniert auch, wenn der Anschluss ausfaellt.
 */
#ifndef TIMESYNC_H
#define TIMESYNC_H

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

esp_err_t timesync_start(const char *server);

/* Wartet, bis die Zeit plausibel ist. */
bool timesync_wait(int timeout_ms);

/* Ist die Zeit brauchbar? Geprueft wird gegen einen festen Stichtag -- ein
 * ESP32 ohne Abgleich startet bei 1970. */
bool timesync_valid(void);

/* Unix-Zeit in Sekunden, oder 0 wenn die Zeit nicht steht. */
int64_t timesync_now(void);

#endif /* TIMESYNC_H */
