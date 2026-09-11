/* Tests der BME280-Kompensationsrechnung.
 *
 * Der Kern ist test_beide_fassungen_stimmen_ueberein: die Ganzzahl- und die
 * Fliesskommafassung sind zwei unabhängig aus dem Datenblatt abgeschriebene
 * Formeln. Stimmen sie überein, ist ein Tippfehler in einer der beiden praktisch
 * ausgeschlossen -- zwei Abschriften irren sich nicht auf dieselbe Weise. Das
 * ersetzt Referenzwerte, die man ohne echten Sensor nicht hat.
 *
 * Die Kalibrierkonstanten sind in der Grössenordnung gewählt, die reale Chips
 * liefern. Genau treffen müssen sie nicht: korrekte Formeln stimmen für jede
 * Konstantenbelegung überein.
 */
#include "bme280_compensate.h"
#include "test_util.h"

#include <stdlib.h>

/* Grössenordnungen aus veröffentlichten Registerauszügen realer Chips. */
static bme280_calib_t nennwerte(void)
{
    bme280_calib_t c = {
        .dig_T1 = 28000, .dig_T2 = 26500, .dig_T3 = 50,
        .dig_P1 = 37000, .dig_P2 = -10700, .dig_P3 = 3024,
        .dig_P4 = 7000,  .dig_P5 = -70,    .dig_P6 = -7,
        .dig_P7 = 15500, .dig_P8 = -14600, .dig_P9 = 6000,
        .dig_H1 = 75,    .dig_H2 = 360,    .dig_H3 = 0,
        .dig_H4 = 320,   .dig_H5 = 50,     .dig_H6 = 30,
    };
    return c;
}

/* Wandlerwerte in der Grössenordnung, die der Chip bei normalem Wetter liefert. */
#define ADC_T_MITTE 519888
#define ADC_P_MITTE 415148
#define ADC_H_MITTE 30000

TEST(test_beide_fassungen_stimmen_ueberein)
{
    bme280_calib_t c = nennwerte();

    for (int i = -8; i <= 8; ++i) {
        int32_t adc_T = ADC_T_MITTE + i * 9000;  /* rund -20 bis +45 Grad */
        int32_t adc_P = ADC_P_MITTE + i * 3000;
        int32_t adc_H = ADC_H_MITTE + i * 2500;
        if (adc_H < 0) {
            adc_H = 0;
        }

        bme280_state_t si = {0}, sd = {0};
        double t_i = bme280_compensate_temperature_int(&c, &si, adc_T) / 100.0;
        double t_d = bme280_compensate_temperature(&c, &sd, adc_T);
        /* Die Ganzzahlfassung rundet auf Hundertstel Grad. */
        CHECK_NEAR(t_i, t_d, 0.01);

        double p_i = bme280_compensate_pressure_int(&c, &si, adc_P) / 256.0;
        double p_d = bme280_compensate_pressure(&c, &sd, adc_P);
        /* Q24.8 löst rund 0,004 Pa auf; die Abweichung bleibt weit unter 1 Pa. */
        CHECK_NEAR(p_i, p_d, 1.0);

        double h_i = bme280_compensate_humidity_int(&c, &si, adc_H) / 1024.0;
        double h_d = bme280_compensate_humidity(&c, &sd, adc_H);
        CHECK_NEAR(h_i, h_d, 0.05);
    }
}

TEST(test_werte_liegen_im_plausiblen_bereich)
{
    bme280_calib_t c = nennwerte();
    bme280_state_t s = {0};

    double t = bme280_compensate_temperature(&c, &s, ADC_T_MITTE);
    CHECK(t > -40.0 && t < 60.0);

    double p = bme280_compensate_pressure(&c, &s, ADC_P_MITTE) / 100.0;  /* hPa */
    CHECK(p > 300.0 && p < 1200.0);

    double h = bme280_compensate_humidity(&c, &s, ADC_H_MITTE);
    CHECK(h >= 0.0 && h <= 100.0);
}

TEST(test_temperatur_steigt_mit_dem_wandlerwert)
{
    bme280_calib_t c = nennwerte();
    double vorher = -1e9;
    for (int i = 0; i < 20; ++i) {
        bme280_state_t s = {0};
        double t = bme280_compensate_temperature(&c, &s, 400000 + i * 12000);
        CHECK(t > vorher);
        vorher = t;
    }
}

TEST(test_druck_faellt_mit_steigendem_wandlerwert)
{
    /* Der Drucksensor zählt invertiert -- ein höherer Wandlerwert heisst weniger
     * Druck. Wer das Vorzeichen verdreht, bekommt eine Wetterstation, deren
     * Drucktendenz systematisch falsch herum zeigt. */
    bme280_calib_t c = nennwerte();
    bme280_state_t s = {0};
    bme280_compensate_temperature(&c, &s, ADC_T_MITTE);

    double vorher = 1e12;
    for (int i = 0; i < 20; ++i) {
        double p = bme280_compensate_pressure(&c, &s, 380000 + i * 4000);
        CHECK(p < vorher);
        vorher = p;
    }
}

TEST(test_feuchte_bleibt_im_erlaubten_bereich)
{
    bme280_calib_t c = nennwerte();
    bme280_state_t s = {0};
    bme280_compensate_temperature(&c, &s, ADC_T_MITTE);

    for (int32_t adc = 0; adc <= 65535; adc += 1024) {
        double h = bme280_compensate_humidity(&c, &s, adc);
        CHECK(h >= 0.0 && h <= 100.0);
    }
}

TEST(test_druck_haengt_von_der_temperatur_ab)
{
    /* t_fine koppelt die Rechnungen. Wer die Temperatur nicht zuerst rechnet,
     * bekommt einen Druck, der auf einem uninitialisierten Wert beruht. */
    bme280_calib_t c = nennwerte();
    bme280_state_t kalt = {0}, warm = {0};
    bme280_compensate_temperature(&c, &kalt, 450000);
    bme280_compensate_temperature(&c, &warm, 560000);

    double p_kalt = bme280_compensate_pressure(&c, &kalt, ADC_P_MITTE);
    double p_warm = bme280_compensate_pressure(&c, &warm, ADC_P_MITTE);
    CHECK(p_kalt != p_warm);
}

TEST(test_kaputte_kalibrierung_ergibt_null_statt_absturz)
{
    bme280_calib_t c = nennwerte();
    c.dig_P1 = 0;  /* würde zur Division durch null führen */
    bme280_state_t s = {0};
    bme280_compensate_temperature(&c, &s, ADC_T_MITTE);
    CHECK(bme280_compensate_pressure(&c, &s, ADC_P_MITTE) == 0.0);
    CHECK(bme280_compensate_pressure_int(&c, &s, ADC_P_MITTE) == 0);
}

TEST(test_kalibrierregister_werden_richtig_zerlegt)
{
    uint8_t c88[26] = {0};
    uint8_t ce1[7] = {0};

    /* dig_T1 = 0x1234, klein-endian abgelegt. */
    c88[0] = 0x34; c88[1] = 0x12;
    /* dig_T2 = -2 */
    c88[2] = 0xFE; c88[3] = 0xFF;
    /* dig_P1 = 0xABCD */
    c88[6] = 0xCD; c88[7] = 0xAB;
    c88[25] = 0x4B;  /* dig_H1 = 75 */

    bme280_calib_t c;
    CHECK(bme280_parse_calibration(c88, ce1, &c));
    CHECK(c.dig_T1 == 0x1234);
    CHECK(c.dig_T2 == -2);
    CHECK(c.dig_P1 == 0xABCD);
    CHECK(c.dig_H1 == 75);
}

TEST(test_h4_und_h5_teilen_sich_ein_byte)
{
    /* Die klassische Fehlerstelle: dig_H4 nimmt die unteren vier Bit von 0xE5,
     * dig_H5 die oberen. Wer beide gleich behandelt, bekommt eine Feuchte, die
     * um zweistellige Prozentwerte danebenliegt. */
    uint8_t c88[26] = {0};
    c88[0] = 0x01; c88[1] = 0x01;  /* dig_T1 ungleich null */
    c88[6] = 0x01; c88[7] = 0x01;  /* dig_P1 ungleich null */

    uint8_t ce1[7] = {0};
    ce1[3] = 0x14;  /* 0xE4: obere acht Bit von dig_H4 */
    ce1[4] = 0x9A;  /* 0xE5: unten H4, oben H5 */
    ce1[5] = 0x02;  /* 0xE6: obere acht Bit von dig_H5 */

    bme280_calib_t c;
    CHECK(bme280_parse_calibration(c88, ce1, &c));
    CHECK(c.dig_H4 == (0x14 * 16 | 0x0A));
    CHECK(c.dig_H5 == (0x02 * 16 | 0x09));
    CHECK(c.dig_H4 != c.dig_H5);
}

TEST(test_nicht_angeschlossener_chip_wird_erkannt)
{
    uint8_t leer88[26] = {0};
    uint8_t leere1[7] = {0};
    bme280_calib_t c;
    /* Lauter Nullen heisst: nichts auf der Adresse, oder falsche Adresse. */
    CHECK(!bme280_parse_calibration(leer88, leere1, &c));
    CHECK(!bme280_parse_calibration(NULL, leere1, &c));
}

TEST(test_rohwerte_aus_dem_burst_read)
{
    /* Druck und Temperatur sind 20 Bit über drei Register, Feuchte 16 Bit
     * über zwei. */
    uint8_t daten[8] = {0x51, 0x23, 0x40, 0x7E, 0xF0, 0x00, 0x75, 0x30};
    int32_t p, t, h;
    bme280_parse_raw(daten, &p, &t, &h);

    CHECK(p == 0x51234);
    CHECK(t == 0x7EF00);
    CHECK(h == 0x7530);
}

TEST(test_abgeschaltete_kanaele_werden_erkannt)
{
    /* Der Chip meldet 0x80000 bzw. 0x8000, wenn ein Kanal aus ist oder noch nie
     * gemessen hat. Diese Werte durch die Formel zu schicken, ergäbe plausible
     * Zahlen -- die aber nichts mit dem Wetter zu tun haben. */
    CHECK(bme280_raw_is_valid(0x7EF00, 0x51234, 0x7530));
    CHECK(!bme280_raw_is_valid(BME280_ADC_DISABLED_TP, 0x51234, 0x7530));
    CHECK(!bme280_raw_is_valid(0x7EF00, BME280_ADC_DISABLED_TP, 0x7530));
    CHECK(!bme280_raw_is_valid(0x7EF00, 0x51234, BME280_ADC_DISABLED_H));
}

int main(void)
{
    RUN(test_beide_fassungen_stimmen_ueberein);
    RUN(test_werte_liegen_im_plausiblen_bereich);
    RUN(test_temperatur_steigt_mit_dem_wandlerwert);
    RUN(test_druck_faellt_mit_steigendem_wandlerwert);
    RUN(test_feuchte_bleibt_im_erlaubten_bereich);
    RUN(test_druck_haengt_von_der_temperatur_ab);
    RUN(test_kaputte_kalibrierung_ergibt_null_statt_absturz);
    RUN(test_kalibrierregister_werden_richtig_zerlegt);
    RUN(test_h4_und_h5_teilen_sich_ein_byte);
    RUN(test_nicht_angeschlossener_chip_wird_erkannt);
    RUN(test_rohwerte_aus_dem_burst_read);
    RUN(test_abgeschaltete_kanaele_werden_erkannt);
    return test_summary("bme280");
}
