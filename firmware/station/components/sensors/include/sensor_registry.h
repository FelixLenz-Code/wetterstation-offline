/* Sensorverwaltung: welcher Sensor ist da, und in welchem Zustand.
 *
 * Spiegelt die Zustandslogik des Servers (wetter/db/sensors.py). Drei Zustaende
 * statt an/aus:
 *
 *   SENSOR_INACTIVE    nicht vorhanden oder abgeschaltet
 *   SENSOR_TEST        liegt zum Entwickeln drinnen -- Werte werden gesendet und
 *                      angezeigt, aber vom Training ausgeschlossen
 *   SENSOR_PRODUCTIVE  haengt am endgueltigen Platz, Werte fliessen ins Training
 *
 * Der Zustand haengt am einzelnen Sensor, nicht an der Station: der BME280 kann
 * laengst draussen produktiv messen, waehrend der AS3935 noch auf dem Tisch liegt.
 *
 * Die Zustaende entstehen aus drei Quellen, in dieser Rangfolge:
 *
 *   1. I2C-Bus-Scan beim Start  -- was ist physisch da?
 *   2. gespeicherte Konfiguration -- was war zuletzt eingestellt?
 *   3. Fernkonfiguration per MQTT -- was sagt der Server?
 *
 * Wichtig ist Punkt 2 vor 1: ein Sensor, der am Bus antwortet, aber bewusst auf
 * inaktiv gesetzt wurde, bleibt inaktiv. Sonst wuerde jeder Neustart eine
 * Einstellung zuruecknehmen, die jemand mit Absicht getroffen hat.
 *
 * Dieses Modul ist frei von ESP-IDF-Abhaengigkeiten und laesst sich deshalb auf dem
 * Rechner testen. Die Anbindung an NVS und I2C liegt in sensor_hw.c.
 */
#ifndef SENSOR_REGISTRY_H
#define SENSOR_REGISTRY_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef enum {
    SENSOR_INACTIVE = 0,
    SENSOR_TEST = 1,
    SENSOR_PRODUCTIVE = 2,
} sensor_state_t;

/* Reihenfolge muss zu kSensorKeys in sensor_registry.c passen. */
typedef enum {
    SENSOR_BME280 = 0,
    SENSOR_WIND_SPEED,
    SENSOR_WIND_VANE,
    SENSOR_RAIN_GAUGE,
    SENSOR_MLX90614,
    SENSOR_AS3935,
    SENSOR_BH1750,
    SENSOR_INA219,
    SENSOR_COUNT,
} sensor_id_t;

/* I2C-Adresse oder 0, wenn der Sensor nicht am Bus haengt (Reed, ADC). */
uint8_t sensor_i2c_address(sensor_id_t id);

/* Kurzname, wie er in MQTT-Nachrichten steht ("bme280", "wind_vane", ...). */
const char *sensor_key(sensor_id_t id);

/* Sucht einen Sensor ueber seinen Kurznamen. Gibt SENSOR_COUNT zurueck, wenn
 * der Name unbekannt ist. */
sensor_id_t sensor_from_key(const char *key);

/* Name eines Zustands fuer die MQTT-Nachricht ("productive", "test", "inactive"). */
const char *sensor_state_name(sensor_state_t state);

/* Umkehrung. Gibt false zurueck, wenn der Name unbekannt ist. */
bool sensor_state_from_name(const char *name, sensor_state_t *out);

typedef struct {
    sensor_state_t state[SENSOR_COUNT];
    bool detected[SENSOR_COUNT];   /* beim Bus-Scan gefunden */
    bool configured[SENSOR_COUNT]; /* Zustand stammt aus gespeicherter Konfiguration */
} sensor_registry_t;

/* Setzt alles auf inaktiv. */
void sensor_registry_init(sensor_registry_t *reg);

/* Traegt das Ergebnis eines Bus-Scans ein.
 *
 * Ein neu gefundener Sensor startet im Testmodus, nicht produktiv: nach dem
 * Anstecken haengt er praktisch nie schon am endgueltigen Platz, und ein paar
 * Stunden Werkstattwerte im Training sind schwerer wieder herauszubekommen als ein
 * Haken, den man einmal setzt. */
void sensor_registry_set_detected(sensor_registry_t *reg, sensor_id_t id, bool present);

/* Uebernimmt einen gespeicherten oder per MQTT gesetzten Zustand.
 *
 * Gibt false zurueck, wenn der Sensor gar nicht erkannt wurde und auf produktiv
 * gesetzt werden soll -- was nicht da ist, kann nicht messen. Sensoren ohne
 * I2C-Adresse (Reed-Kontakte, Windfahne) werden nie erkannt und sind deshalb von
 * dieser Pruefung ausgenommen. */
bool sensor_registry_set_state(sensor_registry_t *reg, sensor_id_t id,
                               sensor_state_t state);

/* Zustand abfragen. */
sensor_state_t sensor_registry_state(const sensor_registry_t *reg, sensor_id_t id);

/* Soll dieser Sensor ueberhaupt ausgelesen werden? */
bool sensor_registry_should_read(const sensor_registry_t *reg, sensor_id_t id);

/* Anzahl der Sensoren in einem bestimmten Zustand. */
int sensor_registry_count(const sensor_registry_t *reg, sensor_state_t state);

/* Packt alle Zustaende in zwei Bytes: zwei Bit je Sensor.
 * Fuer die Ablage in NVS und fuer den Ringpuffer, wo jedes Byte zaehlt. */
uint16_t sensor_registry_pack(const sensor_registry_t *reg);

/* Umkehrung von sensor_registry_pack. Die Erkennungsflags bleiben unberuehrt. */
void sensor_registry_unpack(sensor_registry_t *reg, uint16_t packed);

/* Schreibt die Zustaende als JSON-Objekt ("bme280":"productive",...) in buf.
 * Gibt die Zahl der geschriebenen Zeichen zurueck, oder -1 wenn der Puffer zu
 * klein ist. Ohne die geschweiften Klammern -- der Aufrufer baut die Nachricht. */
int sensor_registry_to_json(const sensor_registry_t *reg, char *buf, size_t len);

#endif /* SENSOR_REGISTRY_H */
