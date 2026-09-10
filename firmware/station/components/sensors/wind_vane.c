#include "wind_vane.h"

#include <math.h>
#include <stdbool.h>
#include <stddef.h>

/* Widerstandstabelle der Fine-Offset-/Argent-Windfahne.
 *
 * Nach Richtung sortiert, nicht nach Widerstand -- die Zuordnung ist bewusst
 * ungeordnet, damit ein Wackelkontakt nicht in eine benachbarte Richtung faellt,
 * sondern in eine voellig andere und damit auffaellt.
 */
typedef struct {
    float ohms;
    float degrees;
} vane_entry_t;

static const vane_entry_t kDefaultTable[WIND_VANE_POSITIONS] = {
    {33000.0f, 0.0f},    {6570.0f, 22.5f},   {8200.0f, 45.0f},    {891.0f, 67.5f},
    {1000.0f, 90.0f},    {688.0f, 112.5f},   {2200.0f, 135.0f},   {1410.0f, 157.5f},
    {3900.0f, 180.0f},   {3140.0f, 202.5f},  {16000.0f, 225.0f},  {14120.0f, 247.5f},
    {120000.0f, 270.0f}, {42120.0f, 292.5f}, {64900.0f, 315.0f},  {21880.0f, 337.5f},
};

/* Die tatsaechlich genutzte Tabelle -- ab Werk die obige, nach dem Kalibrieren die
 * ausgemessene. */
static vane_entry_t kTable[WIND_VANE_POSITIONS];
static bool kTableReady = false;

static void ensure_table(void)
{
    if (!kTableReady) {
        for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
            kTable[i] = kDefaultTable[i];
        }
        kTableReady = true;
    }
}

float wind_vane_min_separation(void)
{
    ensure_table();
    float kleinster = 1.0f;
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        for (int j = i + 1; j < WIND_VANE_POSITIONS; ++j) {
            float a = kTable[i].ohms;
            float b = kTable[j].ohms;
            if (a > b) {
                float t = a;
                a = b;
                b = t;
            }
            /* Grenze, an der beide relativen Fehler gleich gross sind. */
            float grenze = 2.0f * a * b / (a + b);
            float abstand = (grenze - a) / a;
            if (abstand < kleinster) {
                kleinster = abstand;
            }
        }
    }
    return kleinster;
}

void wind_vane_reset_table(void)
{
    kTableReady = false;
    ensure_table();
}

bool wind_vane_set_table(const float *ohms, float tolerance)
{
    if (ohms == NULL) {
        return false;
    }
    vane_entry_t neu[WIND_VANE_POSITIONS];
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        if (!(ohms[i] > 0.0f) || !isfinite(ohms[i])) {
            return false;
        }
        neu[i].ohms = ohms[i];
        neu[i].degrees = kDefaultTable[i].degrees;
    }

    /* Erst uebernehmen, wenn die Tabelle bei dieser Toleranz eindeutig bleibt --
     * sonst laege still eine verdrehte Windrichtung an. */
    vane_entry_t sicherung[WIND_VANE_POSITIONS];
    ensure_table();
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        sicherung[i] = kTable[i];
        kTable[i] = neu[i];
    }
    if (wind_vane_min_separation() <= tolerance) {
        for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
            kTable[i] = sicherung[i];
        }
        return false;
    }
    return true;
}

bool wind_vane_entry(int index, float *ohms_out, float *degrees_out)
{
    if (index < 0 || index >= WIND_VANE_POSITIONS) {
        return false;
    }
    ensure_table();
    if (ohms_out != NULL) {
        *ohms_out = kTable[index].ohms;
    }
    if (degrees_out != NULL) {
        *degrees_out = kTable[index].degrees;
    }
    return true;
}

float wind_vane_resistance(const wind_vane_config_t *cfg, float measured_volts)
{
    if (cfg == NULL || !(cfg->series_ohms > 0.0f) || !(cfg->supply_volts > 0.0f)) {
        return -1.0f;
    }
    /* Der Messpunkt kann die Speisespannung nie erreichen -- dann waere der
     * Fahnenwiderstand null, was die Formel gegen unendlich laufen liesse. */
    if (!(measured_volts > 0.0f) || measured_volts >= cfg->supply_volts) {
        return -1.0f;
    }
    /* Teiler: U_mess = U_ver * R_serie / (R_fahne + R_serie) */
    return cfg->series_ohms * (cfg->supply_volts / measured_volts - 1.0f);
}

float wind_vane_direction_from_ohms(const wind_vane_config_t *cfg, float ohms)
{
    if (cfg == NULL || !(ohms > 0.0f) || !isfinite(ohms)) {
        return WIND_VANE_INVALID;
    }

    ensure_table();
    int best = -1;
    float best_error = 0.0f;
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        /* Relativer statt absoluter Vergleich: die Tabelle spannt von 688 Ohm bis
         * 120 kOhm, ein absolutes Fenster waere unten viel zu weit und oben zu eng. */
        float error = fabsf(ohms - kTable[i].ohms) / kTable[i].ohms;
        if (best < 0 || error < best_error) {
            best = i;
            best_error = error;
        }
    }
    if (best < 0 || best_error > cfg->tolerance) {
        return WIND_VANE_INVALID;
    }
    return kTable[best].degrees;
}

float wind_vane_direction(const wind_vane_config_t *cfg, float measured_volts)
{
    float ohms = wind_vane_resistance(cfg, measured_volts);
    if (ohms < 0.0f) {
        return WIND_VANE_INVALID;
    }
    return wind_vane_direction_from_ohms(cfg, ohms);
}
