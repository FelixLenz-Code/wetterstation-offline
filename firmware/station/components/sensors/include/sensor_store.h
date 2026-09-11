/* Sensorzustaende dauerhaft ablegen.
 *
 * Die Zustaende passen in zwei Byte (zwei Bit je Sensor). Sie ins NVS zu
 * schreiben ist nur noetig, wenn sie sich aendern -- also selten. Genau deshalb
 * ist NVS hier richtig und der Ringpuffer falsch herum gedacht waere: NVS ist
 * fuer wenige, seltene Schreibvorgaenge gebaut.
 */
#ifndef SENSOR_STORE_H
#define SENSOR_STORE_H

#include "esp_err.h"
#include "sensor_registry.h"

/* Laedt gespeicherte Zustaende. Gibt ESP_ERR_NVS_NOT_FOUND zurueck, wenn noch
 * nie etwas gespeichert wurde -- dann gilt, was der Bus-Scan findet. */
esp_err_t sensor_store_load(sensor_registry_t *reg);

/* Speichert, aber nur wenn sich etwas geaendert hat. */
esp_err_t sensor_store_save(const sensor_registry_t *reg);

#endif /* SENSOR_STORE_H */
