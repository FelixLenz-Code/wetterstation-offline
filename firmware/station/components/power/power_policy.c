#include "power_policy.h"

const char *power_mode_name(power_mode_t mode)
{
    switch (mode) {
    case POWER_MODE_NORMAL:
        return "normal";
    case POWER_MODE_SAVING:
        return "sparsam";
    case POWER_MODE_EMERGENCY:
        return "not";
    default:
        return "unbekannt";
    }
}

power_profile_t power_profile(power_mode_t mode)
{
    switch (mode) {
    case POWER_MODE_NORMAL:
        return (power_profile_t){.measure_seconds = 10,
                                 .publish_seconds = 120,
                                 .extra_sensors = true,
                                 .deep_sleep = false};
    case POWER_MODE_SAVING:
        return (power_profile_t){.measure_seconds = 60,
                                 .publish_seconds = 600,
                                 .extra_sensors = false,
                                 .deep_sleep = false};
    case POWER_MODE_EMERGENCY:
    default:
        /* Im Notbetrieb schlaeft die Station tief. Wind- und Regenimpulse gehen
         * dabei verloren -- das ist der Preis, und der Server markiert die
         * Luecke, statt sie als Windstille zu lesen. */
        return (power_profile_t){.measure_seconds = 900,
                                 .publish_seconds = 900,
                                 .extra_sensors = false,
                                 .deep_sleep = true};
    }
}

power_mode_t power_decide(power_mode_t current, float volts)
{
    if (!(volts > 0.0f)) {
        return current;
    }

    /* Herunterschalten sofort -- bei knappem Akku zaehlt jede Minute. */
    if (volts < POWER_V_SAVING) {
        return POWER_MODE_EMERGENCY;
    }

    /* Hochschalten erst mit Abstand. */
    if (volts < POWER_V_SAVING + POWER_HYSTERESIS_V &&
        current == POWER_MODE_EMERGENCY) {
        return POWER_MODE_EMERGENCY;
    }
    if (volts < POWER_V_NORMAL) {
        return POWER_MODE_SAVING;
    }
    if (volts < POWER_V_NORMAL + POWER_HYSTERESIS_V && current != POWER_MODE_NORMAL) {
        return current;
    }
    return POWER_MODE_NORMAL;
}
