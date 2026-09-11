#include "pulse_convert.h"

#include <stddef.h>

float pulse_wind_speed_ms(uint32_t pulses, float seconds, float ms_per_hz)
{
    if (!(seconds > 0.0f)) {
        return 0.0f;
    }
    return ((float)pulses / seconds) * ms_per_hz;
}

float pulse_rain_mm(uint32_t pulses, float mm_per_tip)
{
    return (float)pulses * mm_per_tip;
}

float pulse_gust_ms(const uint32_t *window_pulses, int count, float window_seconds,
                    float ms_per_hz)
{
    if (window_pulses == NULL || count <= 0 || !(window_seconds > 0.0f)) {
        return 0.0f;
    }

    float hoechste = 0.0f;
    for (int i = 0; i < count; ++i) {
        const float v = pulse_wind_speed_ms(window_pulses[i], window_seconds, ms_per_hz);
        if (v > hoechste) {
            hoechste = v;
        }
    }
    return hoechste;
}
