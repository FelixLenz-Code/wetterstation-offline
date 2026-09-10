/* Tests der Windfahnen-Auswertung. */
#include "test_util.h"
#include "wind_vane.h"

static const wind_vane_config_t kCfg = WIND_VANE_CONFIG_DEFAULT;

/* Spannung, die ein bestimmter Fahnenwiderstand am Teiler erzeugt. */
static float volts_for(float ohms)
{
    return kCfg.supply_volts * kCfg.series_ohms / (ohms + kCfg.series_ohms);
}

TEST(test_alle_sechzehn_richtungen_werden_erkannt)
{
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float ohms = 0.0f, grad = 0.0f;
        CHECK(wind_vane_entry(i, &ohms, &grad));
        float gemessen = wind_vane_direction(&kCfg, volts_for(ohms));
        CHECK_NEAR(gemessen, grad, 0.01);
    }
}

TEST(test_richtungen_liegen_auf_dem_22_5_grad_raster)
{
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float grad = 0.0f;
        CHECK(wind_vane_entry(i, NULL, &grad));
        CHECK(grad >= 0.0f && grad < 360.0f);
        CHECK_NEAR(grad / 22.5f, (float)((int)(grad / 22.5f + 0.5f)), 1e-4);
    }
}

TEST(test_jede_richtung_kommt_genau_einmal_vor)
{
    int gesehen[WIND_VANE_POSITIONS] = {0};
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float grad = 0.0f;
        CHECK(wind_vane_entry(i, NULL, &grad));
        int sektor = (int)(grad / 22.5f + 0.5f) % WIND_VANE_POSITIONS;
        CHECK(gesehen[sektor] == 0);
        gesehen[sektor] = 1;
    }
}

TEST(test_widerstand_aus_teilerspannung)
{
    /* Bei gleich grossen Widerstaenden liegt die halbe Speisespannung an. */
    float ohms = wind_vane_resistance(&kCfg, kCfg.supply_volts / 2.0f);
    CHECK_NEAR(ohms, kCfg.series_ohms, 1.0);
}

TEST(test_kleine_streuung_wird_noch_richtig_zugeordnet)
{
    /* 3 % Abweichung liegt sicher innerhalb der Aufloesungsgrenze. */
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float ohms = 0.0f, grad = 0.0f;
        CHECK(wind_vane_entry(i, &ohms, &grad));
        CHECK_NEAR(wind_vane_direction(&kCfg, volts_for(ohms * 1.03f)), grad, 0.01);
        CHECK_NEAR(wind_vane_direction(&kCfg, volts_for(ohms * 0.97f)), grad, 0.01);
    }
}

TEST(test_aufloesungsgrenze_der_tabelle)
{
    /* Dokumentiert die Hardware-Grenze: 891 Ohm (67,5 Grad) und 1000 Ohm
     * (90 Grad) liegen nur 12 % auseinander. Mehr als rund 5,8 % Bauteilstreuung
     * lassen sich deshalb prinzipiell nicht mehr aufloesen -- unabhaengig vom Code. */
    float grenze = wind_vane_min_separation();
    CHECK_NEAR(grenze, 0.0576, 0.001);
    CHECK(kCfg.tolerance <= WIND_VANE_MAX_TOLERANCE);
    CHECK(WIND_VANE_MAX_TOLERANCE < grenze);
}

TEST(test_zu_grosse_streuung_verdreht_die_richtung)
{
    /* Der Grund fuer die enge Toleranz, als Test festgehalten: bei 10 % Abweichung
     * nach unten wird aus 90 Grad still 67,5 Grad. Ohne diesen Test faende man den
     * Fehler erst an einer Wetterfahne, die bei Westwind nach Suedwest zeigt. */
    float ohms = 0.0f, grad = 0.0f;
    CHECK(wind_vane_entry(4, &ohms, &grad));
    CHECK_NEAR(grad, 90.0, 0.01);
    float verdreht = wind_vane_direction(&kCfg, volts_for(ohms * 0.90f));
    CHECK_NEAR(verdreht, 67.5, 0.01);
}

TEST(test_kalibrierung_uebernimmt_ausgemessene_werte)
{
    float eigene[WIND_VANE_POSITIONS];
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float ohms = 0.0f;
        CHECK(wind_vane_entry(i, &ohms, NULL));
        eigene[i] = ohms * 1.04f; /* alle Widerstaende 4 % ueber Nennwert */
    }
    CHECK(wind_vane_set_table(eigene, kCfg.tolerance));

    /* Nach dem Kalibrieren treffen genau diese Werte wieder exakt. */
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float ohms = 0.0f, grad = 0.0f;
        CHECK(wind_vane_entry(i, &ohms, &grad));
        CHECK_NEAR(ohms, eigene[i], 0.5);
        CHECK_NEAR(wind_vane_direction(&kCfg, volts_for(eigene[i])), grad, 0.01);
    }
    wind_vane_reset_table();
}

TEST(test_mehrdeutige_kalibrierung_wird_abgelehnt)
{
    /* Eine ausgemessene Tabelle, in der zwei Richtungen zusammenfallen, darf nicht
     * uebernommen werden -- sonst zeigt die Fahne dauerhaft falsch. */
    float kaputt[WIND_VANE_POSITIONS];
    for (int i = 0; i < WIND_VANE_POSITIONS; ++i) {
        float ohms = 0.0f;
        CHECK(wind_vane_entry(i, &ohms, NULL));
        kaputt[i] = ohms;
    }
    kaputt[3] = kaputt[4]; /* 67,5 Grad und 90 Grad nicht mehr unterscheidbar */
    CHECK(!wind_vane_set_table(kaputt, kCfg.tolerance));

    /* Die alte Tabelle muss unveraendert weitergelten. */
    float ohms = 0.0f;
    CHECK(wind_vane_entry(3, &ohms, NULL));
    CHECK_NEAR(ohms, 891.0, 0.5);
}

TEST(test_unplausible_kalibrierung_wird_abgelehnt)
{
    float kaputt[WIND_VANE_POSITIONS] = {0};
    CHECK(!wind_vane_set_table(kaputt, kCfg.tolerance));
    CHECK(!wind_vane_set_table(NULL, kCfg.tolerance));
}

TEST(test_unplausibler_wert_ergibt_ungueltig)
{
    /* Weit ausserhalb jedes Tabellenwerts -- etwa bei einem Kabelbruch. */
    CHECK(wind_vane_direction_from_ohms(&kCfg, 500000.0f) == WIND_VANE_INVALID);
    CHECK(wind_vane_direction_from_ohms(&kCfg, 1.0f) == WIND_VANE_INVALID);
}

TEST(test_kurzschluss_und_unterbrechung_ergeben_ungueltig)
{
    /* 0 V heisst Fahne kurzgeschlossen, volle Speisespannung heisst offen. */
    CHECK(wind_vane_direction(&kCfg, 0.0f) == WIND_VANE_INVALID);
    CHECK(wind_vane_direction(&kCfg, kCfg.supply_volts) == WIND_VANE_INVALID);
    CHECK(wind_vane_direction(&kCfg, -0.5f) == WIND_VANE_INVALID);
}

TEST(test_tabellenzugriff_prueft_grenzen)
{
    CHECK(!wind_vane_entry(-1, NULL, NULL));
    CHECK(!wind_vane_entry(WIND_VANE_POSITIONS, NULL, NULL));
    CHECK(wind_vane_entry(0, NULL, NULL));
}

int main(void)
{
    RUN(test_alle_sechzehn_richtungen_werden_erkannt);
    RUN(test_richtungen_liegen_auf_dem_22_5_grad_raster);
    RUN(test_jede_richtung_kommt_genau_einmal_vor);
    RUN(test_widerstand_aus_teilerspannung);
    RUN(test_kleine_streuung_wird_noch_richtig_zugeordnet);
    RUN(test_aufloesungsgrenze_der_tabelle);
    RUN(test_zu_grosse_streuung_verdreht_die_richtung);
    RUN(test_kalibrierung_uebernimmt_ausgemessene_werte);
    RUN(test_mehrdeutige_kalibrierung_wird_abgelehnt);
    RUN(test_unplausible_kalibrierung_wird_abgelehnt);
    RUN(test_unplausibler_wert_ergibt_ungueltig);
    RUN(test_kurzschluss_und_unterbrechung_ergeben_ungueltig);
    RUN(test_tabellenzugriff_prueft_grenzen);
    return test_summary("wind_vane");
}
