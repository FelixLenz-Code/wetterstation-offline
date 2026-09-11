#include "bme280_compensate.h"

#include <stddef.h>

/* --- Ganzzahlfassung -------------------------------------------------------
 *
 * Übernommen aus dem BME280-Datenblatt, Anhang A. Die Schiebeoperationen und
 * Zahlen stehen bewusst genau so da wie dort -- wer sie "aufräumt", macht den
 * Vergleich mit dem Datenblatt unmöglich und das Ergebnis unprüfbar.
 */

int32_t bme280_compensate_temperature_int(const bme280_calib_t *c,
                                          bme280_state_t *state, int32_t adc_T)
{
    int32_t var1, var2, T;

    var1 = ((((adc_T >> 3) - ((int32_t)c->dig_T1 << 1))) * ((int32_t)c->dig_T2)) >> 11;
    var2 = (((((adc_T >> 4) - ((int32_t)c->dig_T1)) *
              ((adc_T >> 4) - ((int32_t)c->dig_T1))) >> 12) *
            ((int32_t)c->dig_T3)) >> 14;

    state->t_fine = var1 + var2;
    T = (state->t_fine * 5 + 128) >> 8;
    return T;
}

uint32_t bme280_compensate_pressure_int(const bme280_calib_t *c,
                                        const bme280_state_t *state, int32_t adc_P)
{
    int64_t var1, var2, p;

    var1 = ((int64_t)state->t_fine) - 128000;
    var2 = var1 * var1 * (int64_t)c->dig_P6;
    var2 = var2 + ((var1 * (int64_t)c->dig_P5) << 17);
    var2 = var2 + (((int64_t)c->dig_P4) << 35);
    var1 = ((var1 * var1 * (int64_t)c->dig_P3) >> 8) +
           ((var1 * (int64_t)c->dig_P2) << 12);
    var1 = (((((int64_t)1) << 47) + var1)) * ((int64_t)c->dig_P1) >> 33;

    if (var1 == 0) {
        /* Division durch null -- kann bei kaputten Kalibrierdaten auftreten. */
        return 0;
    }

    p = 1048576 - adc_P;
    p = (((p << 31) - var2) * 3125) / var1;
    var1 = (((int64_t)c->dig_P9) * (p >> 13) * (p >> 13)) >> 25;
    var2 = (((int64_t)c->dig_P8) * p) >> 19;
    p = ((p + var1 + var2) >> 8) + (((int64_t)c->dig_P7) << 4);
    return (uint32_t)p;
}

uint32_t bme280_compensate_humidity_int(const bme280_calib_t *c,
                                        const bme280_state_t *state, int32_t adc_H)
{
    int32_t v_x1_u32r;

    v_x1_u32r = (state->t_fine - ((int32_t)76800));
    v_x1_u32r = (((((adc_H << 14) - (((int32_t)c->dig_H4) << 20) -
                    (((int32_t)c->dig_H5) * v_x1_u32r)) +
                   ((int32_t)16384)) >> 15) *
                 (((((((v_x1_u32r * ((int32_t)c->dig_H6)) >> 10) *
                      (((v_x1_u32r * ((int32_t)c->dig_H3)) >> 11) +
                       ((int32_t)32768))) >> 10) +
                    ((int32_t)2097152)) *
                       ((int32_t)c->dig_H2) +
                   8192) >>
                  14));
    v_x1_u32r = (v_x1_u32r - (((((v_x1_u32r >> 15) * (v_x1_u32r >> 15)) >> 7) *
                               ((int32_t)c->dig_H1)) >> 4));
    v_x1_u32r = (v_x1_u32r < 0 ? 0 : v_x1_u32r);
    v_x1_u32r = (v_x1_u32r > 419430400 ? 419430400 : v_x1_u32r);
    return (uint32_t)(v_x1_u32r >> 12);
}

/* --- Fliesskommafassung (Datenblatt, Anhang B) ---------------------------- */

double bme280_compensate_temperature(const bme280_calib_t *c,
                                     bme280_state_t *state, int32_t adc_T)
{
    double var1, var2;

    var1 = (((double)adc_T) / 16384.0 - ((double)c->dig_T1) / 1024.0) *
           ((double)c->dig_T2);
    var2 = ((((double)adc_T) / 131072.0 - ((double)c->dig_T1) / 8192.0) *
            (((double)adc_T) / 131072.0 - ((double)c->dig_T1) / 8192.0)) *
           ((double)c->dig_T3);

    state->t_fine_d = var1 + var2;
    return (var1 + var2) / 5120.0;
}

double bme280_compensate_pressure(const bme280_calib_t *c,
                                  const bme280_state_t *state, int32_t adc_P)
{
    double var1, var2, p;

    var1 = (state->t_fine_d / 2.0) - 64000.0;
    var2 = var1 * var1 * ((double)c->dig_P6) / 32768.0;
    var2 = var2 + var1 * ((double)c->dig_P5) * 2.0;
    var2 = (var2 / 4.0) + (((double)c->dig_P4) * 65536.0);
    var1 = (((double)c->dig_P3) * var1 * var1 / 524288.0 +
            ((double)c->dig_P2) * var1) /
           524288.0;
    var1 = (1.0 + var1 / 32768.0) * ((double)c->dig_P1);

    if (var1 == 0.0) {
        return 0.0;
    }

    p = 1048576.0 - (double)adc_P;
    p = (p - (var2 / 4096.0)) * 6250.0 / var1;
    var1 = ((double)c->dig_P9) * p * p / 2147483648.0;
    var2 = p * ((double)c->dig_P8) / 32768.0;
    p = p + (var1 + var2 + ((double)c->dig_P7)) / 16.0;
    return p;
}

double bme280_compensate_humidity(const bme280_calib_t *c,
                                  const bme280_state_t *state, int32_t adc_H)
{
    double var_H;

    var_H = state->t_fine_d - 76800.0;
    var_H = (adc_H - (((double)c->dig_H4) * 64.0 +
                      ((double)c->dig_H5) / 16384.0 * var_H)) *
            (((double)c->dig_H2) / 65536.0 *
             (1.0 + ((double)c->dig_H6) / 67108864.0 * var_H *
                        (1.0 + ((double)c->dig_H3) / 67108864.0 * var_H)));
    var_H = var_H * (1.0 - ((double)c->dig_H1) * var_H / 524288.0);

    if (var_H > 100.0) {
        var_H = 100.0;
    } else if (var_H < 0.0) {
        var_H = 0.0;
    }
    return var_H;
}

/* --- Register auspacken --------------------------------------------------- */

static uint16_t u16le(const uint8_t *p)
{
    return (uint16_t)((uint16_t)p[1] << 8 | p[0]);
}

static int16_t s16le(const uint8_t *p)
{
    return (int16_t)u16le(p);
}

bool bme280_parse_calibration(const uint8_t *calib_88, const uint8_t *calib_e1,
                              bme280_calib_t *out)
{
    if (calib_88 == NULL || calib_e1 == NULL || out == NULL) {
        return false;
    }

    out->dig_T1 = u16le(&calib_88[0]);
    out->dig_T2 = s16le(&calib_88[2]);
    out->dig_T3 = s16le(&calib_88[4]);

    out->dig_P1 = u16le(&calib_88[6]);
    out->dig_P2 = s16le(&calib_88[8]);
    out->dig_P3 = s16le(&calib_88[10]);
    out->dig_P4 = s16le(&calib_88[12]);
    out->dig_P5 = s16le(&calib_88[14]);
    out->dig_P6 = s16le(&calib_88[16]);
    out->dig_P7 = s16le(&calib_88[18]);
    out->dig_P8 = s16le(&calib_88[20]);
    out->dig_P9 = s16le(&calib_88[22]);

    /* calib_88[24] ist reserviert, dig_H1 steht auf 0xA1. */
    out->dig_H1 = calib_88[25];

    out->dig_H2 = s16le(&calib_e1[0]);
    out->dig_H3 = calib_e1[2];
    /* dig_H4 und dig_H5 teilen sich ein Byte: H4 nutzt die unteren vier Bit von
     * 0xE5, H5 die oberen. Das ist die Stelle, an der die meisten Portierungen
     * danebengreifen. */
    out->dig_H4 = (int16_t)(((int16_t)(int8_t)calib_e1[3] * 16) |
                            (calib_e1[4] & 0x0F));
    out->dig_H5 = (int16_t)(((int16_t)(int8_t)calib_e1[5] * 16) |
                            ((calib_e1[4] >> 4) & 0x0F));
    out->dig_H6 = (int8_t)calib_e1[6];

    /* dig_T1 und dig_P1 sind nie null; ein Chip, der lauter Nullen liefert, ist
     * nicht angeschlossen oder antwortet auf der falschen Adresse. */
    return out->dig_T1 != 0 && out->dig_P1 != 0;
}

void bme280_parse_raw(const uint8_t *data, int32_t *adc_P, int32_t *adc_T,
                      int32_t *adc_H)
{
    if (data == NULL) {
        return;
    }
    if (adc_P != NULL) {
        *adc_P = (int32_t)(((uint32_t)data[0] << 12) | ((uint32_t)data[1] << 4) |
                           ((uint32_t)data[2] >> 4));
    }
    if (adc_T != NULL) {
        *adc_T = (int32_t)(((uint32_t)data[3] << 12) | ((uint32_t)data[4] << 4) |
                           ((uint32_t)data[5] >> 4));
    }
    if (adc_H != NULL) {
        *adc_H = (int32_t)(((uint32_t)data[6] << 8) | (uint32_t)data[7]);
    }
}

bool bme280_raw_is_valid(int32_t adc_T, int32_t adc_P, int32_t adc_H)
{
    return adc_T != BME280_ADC_DISABLED_TP && adc_P != BME280_ADC_DISABLED_TP &&
           adc_H != BME280_ADC_DISABLED_H;
}
