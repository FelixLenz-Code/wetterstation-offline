/* Tests der Betriebsart-Entscheidung.
 *
 * Die Hysterese ist der Kern. Ohne sie schaltet die Station im Grenzbereich
 * dauernd hin und her: bei Sonnenaufgang steigt die Spannung kurz, sie schaltet
 * hoch, die groessere Last zieht sie wieder herunter -- und das im Minutentakt.
 * Jeder Wechsel kostet dabei mehr, als er spart.
 */
#include "power_policy.h"
#include "test_util.h"

TEST(test_voller_akku_ergibt_normalbetrieb)
{
    CHECK(power_decide(POWER_MODE_NORMAL, 4.05f) == POWER_MODE_NORMAL);
    CHECK(power_decide(POWER_MODE_SAVING, 4.05f) == POWER_MODE_NORMAL);
    CHECK(power_decide(POWER_MODE_EMERGENCY, 4.05f) == POWER_MODE_NORMAL);
}

TEST(test_leerer_akku_ergibt_notbetrieb)
{
    CHECK(power_decide(POWER_MODE_NORMAL, 3.30f) == POWER_MODE_EMERGENCY);
    CHECK(power_decide(POWER_MODE_SAVING, 3.30f) == POWER_MODE_EMERGENCY);
}

TEST(test_herunterschalten_geschieht_sofort)
{
    /* Bei knappem Akku zaehlt jede Minute -- hier darf keine Hysterese bremsen. */
    CHECK(power_decide(POWER_MODE_NORMAL, POWER_V_NORMAL - 0.01f) ==
          POWER_MODE_SAVING);
    CHECK(power_decide(POWER_MODE_SAVING, POWER_V_SAVING - 0.01f) ==
          POWER_MODE_EMERGENCY);
}

TEST(test_hochschalten_braucht_abstand)
{
    /* Knapp ueber der Schwelle bleibt es bei der sparsameren Art. */
    CHECK(power_decide(POWER_MODE_SAVING, POWER_V_NORMAL + 0.01f) ==
          POWER_MODE_SAVING);
    CHECK(power_decide(POWER_MODE_EMERGENCY, POWER_V_SAVING + 0.01f) ==
          POWER_MODE_EMERGENCY);

    /* Mit genug Abstand dagegen schon. */
    CHECK(power_decide(POWER_MODE_SAVING, POWER_V_NORMAL + POWER_HYSTERESIS_V +
                                              0.01f) == POWER_MODE_NORMAL);
    CHECK(power_decide(POWER_MODE_EMERGENCY, POWER_V_SAVING + POWER_HYSTERESIS_V +
                                                 0.01f) == POWER_MODE_SAVING);
}

TEST(test_kein_flattern_im_grenzbereich)
{
    /* Der eigentliche Zweck: eine Spannung, die um die Schwelle schwankt, darf
     * nicht bei jeder Messung eine andere Betriebsart ergeben. */
    power_mode_t modus = POWER_MODE_SAVING;
    int wechsel = 0;
    const float schwankung[] = {3.59f, 3.61f, 3.58f, 3.62f, 3.60f, 3.63f,
                                3.59f, 3.64f, 3.61f, 3.58f};
    for (int i = 0; i < 10; ++i) {
        power_mode_t neu = power_decide(modus, schwankung[i]);
        if (neu != modus) {
            wechsel++;
        }
        modus = neu;
    }
    CHECK(wechsel == 0);
}

TEST(test_echter_anstieg_schaltet_doch_hoch)
{
    /* Die Hysterese darf nicht so stark sein, dass ein ladender Akku nie wieder
     * in den Normalbetrieb kommt. */
    power_mode_t modus = POWER_MODE_EMERGENCY;
    for (float v = 3.30f; v <= 4.10f; v += 0.02f) {
        modus = power_decide(modus, v);
    }
    CHECK(modus == POWER_MODE_NORMAL);
}

TEST(test_unbrauchbare_messung_aendert_nichts)
{
    /* Lieber weitermachen als wegen eines Messfehlers in den Notbetrieb fallen. */
    CHECK(power_decide(POWER_MODE_NORMAL, -1.0f) == POWER_MODE_NORMAL);
    CHECK(power_decide(POWER_MODE_SAVING, 0.0f) == POWER_MODE_SAVING);
}

TEST(test_sparsamere_art_misst_seltener)
{
    power_profile_t normal = power_profile(POWER_MODE_NORMAL);
    power_profile_t sparsam = power_profile(POWER_MODE_SAVING);
    power_profile_t not_ = power_profile(POWER_MODE_EMERGENCY);

    CHECK(normal.measure_seconds < sparsam.measure_seconds);
    CHECK(sparsam.measure_seconds < not_.measure_seconds);
    CHECK(normal.publish_seconds < sparsam.publish_seconds);
}

TEST(test_zusatzsensoren_nur_im_normalbetrieb)
{
    /* MLX90614, BH1750 und AS3935 sind Komfort -- Druck und Temperatur sind es
     * nicht. Bei knappem Akku fallen zuerst die Zusatzsensoren weg. */
    CHECK(power_profile(POWER_MODE_NORMAL).extra_sensors);
    CHECK(!power_profile(POWER_MODE_SAVING).extra_sensors);
    CHECK(!power_profile(POWER_MODE_EMERGENCY).extra_sensors);
}

TEST(test_tiefschlaf_nur_im_notbetrieb)
{
    /* Im Tiefschlaf gehen Wind- und Regenimpulse verloren. Das ist nur im
     * Notbetrieb hinnehmbar. */
    CHECK(!power_profile(POWER_MODE_NORMAL).deep_sleep);
    CHECK(!power_profile(POWER_MODE_SAVING).deep_sleep);
    CHECK(power_profile(POWER_MODE_EMERGENCY).deep_sleep);
}

TEST(test_jede_betriebsart_hat_einen_namen)
{
    for (int m = 0; m < POWER_MODE_COUNT; ++m) {
        CHECK(power_mode_name((power_mode_t)m)[0] != '\0');
    }
}

int main(void)
{
    RUN(test_voller_akku_ergibt_normalbetrieb);
    RUN(test_leerer_akku_ergibt_notbetrieb);
    RUN(test_herunterschalten_geschieht_sofort);
    RUN(test_hochschalten_braucht_abstand);
    RUN(test_kein_flattern_im_grenzbereich);
    RUN(test_echter_anstieg_schaltet_doch_hoch);
    RUN(test_unbrauchbare_messung_aendert_nichts);
    RUN(test_sparsamere_art_misst_seltener);
    RUN(test_zusatzsensoren_nur_im_normalbetrieb);
    RUN(test_tiefschlaf_nur_im_notbetrieb);
    RUN(test_jede_betriebsart_hat_einen_namen);
    return test_summary("power_policy");
}
