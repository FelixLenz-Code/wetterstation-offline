/* Bindet den Ringpuffer an die Flash-Partition "messdaten".
 *
 * Der Ringpuffer selbst kennt kein ESP-IDF -- er bekommt drei Funktionszeiger
 * fuer Lesen, Schreiben und Loeschen. Hier werden sie mit der echten Partition
 * verbunden; im Test mit einem Speicherabbild, das die NOR-Eigenheiten nachbildet.
 */
#ifndef FLASH_BACKEND_H
#define FLASH_BACKEND_H

#include "esp_err.h"
#include "ringbuffer.h"

/* Sucht die Partition und fuellt die Zugriffsstruktur. */
esp_err_t flash_backend_init(rb_flash_t *out);

#endif /* FLASH_BACKEND_H */
