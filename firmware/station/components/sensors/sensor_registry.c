#include "sensor_registry.h"

#include <stdio.h>
#include <string.h>

/* Reihenfolge muss zu sensor_id_t passen. */
static const char *const kSensorKeys[SENSOR_COUNT] = {
    "bme280", "wind_speed", "wind_vane", "rain_gauge",
    "mlx90614", "as3935", "bh1750", "ina219",
};

/* 0 heisst: haengt nicht am I2C-Bus und kann deshalb nicht erkannt werden. */
static const uint8_t kI2cAddress[SENSOR_COUNT] = {
    [SENSOR_BME280] = 0x76,
    [SENSOR_WIND_SPEED] = 0x00,
    [SENSOR_WIND_VANE] = 0x00,
    [SENSOR_RAIN_GAUGE] = 0x00,
    [SENSOR_MLX90614] = 0x5A,
    [SENSOR_AS3935] = 0x03,
    [SENSOR_BH1750] = 0x23,
    [SENSOR_INA219] = 0x40,
};

static const char *const kStateNames[] = {"inactive", "test", "productive"};

uint8_t sensor_i2c_address(sensor_id_t id)
{
    if (id < 0 || id >= SENSOR_COUNT) {
        return 0;
    }
    return kI2cAddress[id];
}

const char *sensor_key(sensor_id_t id)
{
    if (id < 0 || id >= SENSOR_COUNT) {
        return "";
    }
    return kSensorKeys[id];
}

sensor_id_t sensor_from_key(const char *key)
{
    if (key == NULL) {
        return SENSOR_COUNT;
    }
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        if (strcmp(key, kSensorKeys[i]) == 0) {
            return (sensor_id_t)i;
        }
    }
    return SENSOR_COUNT;
}

const char *sensor_state_name(sensor_state_t state)
{
    if (state < SENSOR_INACTIVE || state > SENSOR_PRODUCTIVE) {
        return kStateNames[SENSOR_INACTIVE];
    }
    return kStateNames[state];
}

bool sensor_state_from_name(const char *name, sensor_state_t *out)
{
    if (name == NULL || out == NULL) {
        return false;
    }
    for (int i = 0; i <= SENSOR_PRODUCTIVE; ++i) {
        if (strcmp(name, kStateNames[i]) == 0) {
            *out = (sensor_state_t)i;
            return true;
        }
    }
    return false;
}

void sensor_registry_init(sensor_registry_t *reg)
{
    if (reg == NULL) {
        return;
    }
    memset(reg, 0, sizeof(*reg));
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        reg->state[i] = SENSOR_INACTIVE;
    }
}

void sensor_registry_set_detected(sensor_registry_t *reg, sensor_id_t id, bool present)
{
    if (reg == NULL || id < 0 || id >= SENSOR_COUNT) {
        return;
    }
    reg->detected[id] = present;

    if (!present) {
        /* Weg ist weg: ein abgezogener Sensor kann nicht produktiv sein. Der
         * Server sieht am Zustandswechsel, dass ab hier eine Luecke beginnt. */
        reg->state[id] = SENSOR_INACTIVE;
        return;
    }
    if (reg->configured[id]) {
        /* Eine bewusst getroffene Einstellung darf ein Neustart nicht zuruecknehmen. */
        return;
    }
    /* Frisch entdeckt: Testmodus. Nach dem Anstecken haengt ein Sensor praktisch
     * nie schon am endgueltigen Platz. */
    reg->state[id] = SENSOR_TEST;
}

bool sensor_registry_set_state(sensor_registry_t *reg, sensor_id_t id,
                               sensor_state_t state)
{
    if (reg == NULL || id < 0 || id >= SENSOR_COUNT) {
        return false;
    }
    if (state < SENSOR_INACTIVE || state > SENSOR_PRODUCTIVE) {
        return false;
    }
    /* Sensoren am I2C-Bus muessen erkannt worden sein, bevor sie messen duerfen.
     * Reed-Kontakte und die Windfahne haengen an GPIO bzw. ADC und lassen sich
     * nicht abfragen -- fuer sie gilt die Pruefung nicht. */
    if (state != SENSOR_INACTIVE && kI2cAddress[id] != 0 && !reg->detected[id]) {
        return false;
    }
    reg->state[id] = state;
    reg->configured[id] = true;
    return true;
}

sensor_state_t sensor_registry_state(const sensor_registry_t *reg, sensor_id_t id)
{
    if (reg == NULL || id < 0 || id >= SENSOR_COUNT) {
        return SENSOR_INACTIVE;
    }
    return reg->state[id];
}

bool sensor_registry_should_read(const sensor_registry_t *reg, sensor_id_t id)
{
    return sensor_registry_state(reg, id) != SENSOR_INACTIVE;
}

int sensor_registry_count(const sensor_registry_t *reg, sensor_state_t state)
{
    if (reg == NULL) {
        return 0;
    }
    int n = 0;
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        if (reg->state[i] == state) {
            n++;
        }
    }
    return n;
}

uint16_t sensor_registry_pack(const sensor_registry_t *reg)
{
    if (reg == NULL) {
        return 0;
    }
    uint16_t packed = 0;
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        packed |= (uint16_t)((reg->state[i] & 0x3) << (i * 2));
    }
    return packed;
}

void sensor_registry_unpack(sensor_registry_t *reg, uint16_t packed)
{
    if (reg == NULL) {
        return;
    }
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        uint8_t roh = (uint8_t)((packed >> (i * 2)) & 0x3);
        /* 3 ist kein gueltiger Zustand -- entsteht nur aus geloeschtem Flash
         * (alle Bits 1). Dann gilt inaktiv. */
        reg->state[i] = (roh <= SENSOR_PRODUCTIVE) ? (sensor_state_t)roh
                                                   : SENSOR_INACTIVE;
        reg->configured[i] = true;
    }
}

int sensor_registry_to_json(const sensor_registry_t *reg, char *buf, size_t len)
{
    if (reg == NULL || buf == NULL || len == 0) {
        return -1;
    }
    size_t used = 0;
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        int n = snprintf(buf + used, len - used, "%s\"%s\":\"%s\"",
                         (i == 0) ? "" : ",", kSensorKeys[i],
                         sensor_state_name(reg->state[i]));
        if (n < 0 || (size_t)n >= len - used) {
            return -1;
        }
        used += (size_t)n;
    }
    return (int)used;
}
