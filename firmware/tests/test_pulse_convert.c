/* Tests der Impuls-Umrechnung.
 *
 * Die Konstanten sind die fehleranfaelligste Stelle der ganzen Station: ein
 * falscher Faktor faellt niemandem auf, weil das Ergebnis plausibel aussieht.
 * Eine Wetterstation, die 28 Prozent zu wenig Regen meldet, sieht genauso aus
 * wie eine, die richtig misst -- nur das Modell lernt den Fehler mit.
 */
#include "pulse_convert.h"
#include "test_util.h"

TEST(test_regenkonstante_ist_elf_tausendstel_zoll)
{
    /* 0,2794 mm und nicht 0,2: die verbreiteten Wippen stammen aus dem US-Markt
     * und kippen bei elf Tausendstel Zoll. Nicht bei einem Hundertstel -- das
     * waeren 0,254 mm, und genau diese Verwechslung hat dieser Test schon einmal
     * gefangen. */
    CHECK_NEAR(RAIN_MM_PER_TIP_DEFAULT, 0.011 * 25.4, 1e-6);
    /* Zur Abgrenzung: ein Hundertstel Zoll waere spuerbar weniger. */
    CHECK(RAIN_MM_PER_TIP_DEFAULT > 25.4 / 100.0);
    /* Und die Verwechslung kostet genau soviel: */
    double zu_wenig = 1.0 - 0.2 / RAIN_MM_PER_TIP_DEFAULT;
    CHECK_NEAR(zu_wenig, 0.284, 0.01);
}

TEST(test_regenmenge_zaehlt_linear)
{
    CHECK_NEAR(pulse_rain_mm(0, RAIN_MM_PER_TIP_DEFAULT), 0.0, 1e-6);
    CHECK_NEAR(pulse_rain_mm(1, RAIN_MM_PER_TIP_DEFAULT), 0.2794, 1e-4);
    CHECK_NEAR(pulse_rain_mm(10, RAIN_MM_PER_TIP_DEFAULT), 2.794, 1e-3);
    /* Ein kraeftiger Landregen von 20 mm sind rund 72 Kippungen. */
    CHECK_NEAR(pulse_rain_mm(72, RAIN_MM_PER_TIP_DEFAULT), 20.1, 0.1);
}

TEST(test_eigene_kalibrierung_wird_genutzt)
{
    /* Nach dem Auslitern kann der Faktor abweichen -- die Funktion nimmt ihn
     * als Parameter, damit man ihn nicht im Code suchen muss. */
    CHECK_NEAR(pulse_rain_mm(10, 0.25f), 2.5, 1e-4);
}

TEST(test_windgeschwindigkeit_bei_einem_hertz)
{
    /* Herstellerangabe: eine Umdrehung je Sekunde sind 2,4 km/h. */
    float v = pulse_wind_speed_ms(1, 1.0f, WIND_MS_PER_HZ_DEFAULT);
    CHECK_NEAR(v * 3.6, 2.4, 0.01);
}

TEST(test_windgeschwindigkeit_skaliert_mit_der_zeit)
{
    /* Zehn Impulse in zehn Sekunden sind dasselbe wie einer in einer. */
    CHECK_NEAR(pulse_wind_speed_ms(10, 10.0f, WIND_MS_PER_HZ_DEFAULT),
               pulse_wind_speed_ms(1, 1.0f, WIND_MS_PER_HZ_DEFAULT), 1e-6);
    /* Und doppelt so viele Impulse sind doppelt so schnell. */
    CHECK_NEAR(pulse_wind_speed_ms(20, 10.0f, WIND_MS_PER_HZ_DEFAULT),
               2.0 * pulse_wind_speed_ms(10, 10.0f, WIND_MS_PER_HZ_DEFAULT), 1e-5);
}

TEST(test_windstille_ist_null_und_kein_unsinn)
{
    CHECK_NEAR(pulse_wind_speed_ms(0, 10.0f, WIND_MS_PER_HZ_DEFAULT), 0.0, 1e-9);
    /* Zeitraum null darf nicht durch null teilen. */
    CHECK_NEAR(pulse_wind_speed_ms(5, 0.0f, WIND_MS_PER_HZ_DEFAULT), 0.0, 1e-9);
    CHECK_NEAR(pulse_wind_speed_ms(5, -1.0f, WIND_MS_PER_HZ_DEFAULT), 0.0, 1e-9);
}

TEST(test_sturmgeschwindigkeit_bleibt_plausibel)
{
    /* Ein Orkan mit 120 km/h sind rund 50 Impulse je Sekunde. */
    float v = pulse_wind_speed_ms(50, 1.0f, WIND_MS_PER_HZ_DEFAULT);
    CHECK_NEAR(v * 3.6, 120.0, 1.0);
}

TEST(test_boee_ist_das_maximum_der_fenster)
{
    /* Windstille, dann eine kurze Boee, dann wieder ruhig. Das Mittel waere
     * harmlos -- die Boee ist aber genau das, wovor gewarnt werden soll. */
    const uint32_t fenster[] = {3, 4, 3, 25, 4, 3};
    float boee = pulse_gust_ms(fenster, 6, GUST_WINDOW_SECONDS,
                               WIND_MS_PER_HZ_DEFAULT);
    float mittel = pulse_wind_speed_ms(3 + 4 + 3 + 25 + 4 + 3,
                                       6 * GUST_WINDOW_SECONDS,
                                       WIND_MS_PER_HZ_DEFAULT);
    CHECK(boee > mittel * 2.0f);
    CHECK_NEAR(boee, pulse_wind_speed_ms(25, GUST_WINDOW_SECONDS,
                                         WIND_MS_PER_HZ_DEFAULT), 1e-6);
}

TEST(test_boeenfenster_folgt_der_wmo)
{
    /* Die WMO definiert die Boee als Drei-Sekunden-Mittel. Kuerzer misst
     * einzelne Umdrehungen, laenger glaettet die Boee weg. */
    CHECK_NEAR(GUST_WINDOW_SECONDS, 3.0, 1e-9);
}

TEST(test_boee_ohne_daten_ist_null)
{
    CHECK_NEAR(pulse_gust_ms(NULL, 5, 3.0f, WIND_MS_PER_HZ_DEFAULT), 0.0, 1e-9);
    const uint32_t leer[] = {0};
    CHECK_NEAR(pulse_gust_ms(leer, 0, 3.0f, WIND_MS_PER_HZ_DEFAULT), 0.0, 1e-9);
    CHECK_NEAR(pulse_gust_ms(leer, 1, 0.0f, WIND_MS_PER_HZ_DEFAULT), 0.0, 1e-9);
}

int main(void)
{
    RUN(test_regenkonstante_ist_elf_tausendstel_zoll);
    RUN(test_regenmenge_zaehlt_linear);
    RUN(test_eigene_kalibrierung_wird_genutzt);
    RUN(test_windgeschwindigkeit_bei_einem_hertz);
    RUN(test_windgeschwindigkeit_skaliert_mit_der_zeit);
    RUN(test_windstille_ist_null_und_kein_unsinn);
    RUN(test_sturmgeschwindigkeit_bleibt_plausibel);
    RUN(test_boee_ist_das_maximum_der_fenster);
    RUN(test_boeenfenster_folgt_der_wmo);
    RUN(test_boee_ohne_daten_ist_null);
    return test_summary("pulse_convert");
}
