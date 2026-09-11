/* Tests der Sensorverwaltung.
 *
 * Kernfrage aller Tests: kann ein Sensor, der zum Entwickeln drinnen liegt,
 * versehentlich als produktiv durchgehen? Das waere der teuerste Fehler des
 * Projekts -- Werkstattwerte im Training bekommt man nur schwer wieder heraus.
 */
#include "sensor_registry.h"
#include "test_util.h"

TEST(test_startzustand_ist_alles_inaktiv)
{
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        CHECK(sensor_registry_state(&reg, (sensor_id_t)i) == SENSOR_INACTIVE);
        CHECK(!sensor_registry_should_read(&reg, (sensor_id_t)i));
    }
}

TEST(test_neu_erkannter_sensor_startet_im_testmodus)
{
    /* Die wichtigste Regel: nach dem Anstecken haengt ein Sensor praktisch nie
     * schon am endgueltigen Platz. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_registry_set_detected(&reg, SENSOR_BME280, true);
    CHECK(sensor_registry_state(&reg, SENSOR_BME280) == SENSOR_TEST);
    CHECK(sensor_registry_should_read(&reg, SENSOR_BME280));
}

TEST(test_bewusste_einstellung_ueberlebt_den_neustart)
{
    /* Ein Neustart darf keine Einstellung zuruecknehmen, die jemand mit Absicht
     * getroffen hat -- sonst waere der Sensor nach jedem Stromausfall wieder im
     * Testmodus und seine Werte fehlten im Training. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_registry_set_detected(&reg, SENSOR_BME280, true);
    CHECK(sensor_registry_set_state(&reg, SENSOR_BME280, SENSOR_PRODUCTIVE));

    /* Neustart: gespeicherte Zustaende laden, dann Bus-Scan. */
    uint16_t gespeichert = sensor_registry_pack(&reg);
    sensor_registry_t neu;
    sensor_registry_init(&neu);
    sensor_registry_unpack(&neu, gespeichert);
    sensor_registry_set_detected(&neu, SENSOR_BME280, true);

    CHECK(sensor_registry_state(&neu, SENSOR_BME280) == SENSOR_PRODUCTIVE);
}

TEST(test_abgezogener_sensor_wird_inaktiv)
{
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_registry_set_detected(&reg, SENSOR_MLX90614, true);
    CHECK(sensor_registry_set_state(&reg, SENSOR_MLX90614, SENSOR_PRODUCTIVE));

    sensor_registry_set_detected(&reg, SENSOR_MLX90614, false);
    CHECK(sensor_registry_state(&reg, SENSOR_MLX90614) == SENSOR_INACTIVE);
    CHECK(!sensor_registry_should_read(&reg, SENSOR_MLX90614));
}

TEST(test_nicht_erkannter_i2c_sensor_kann_nicht_produktiv_werden)
{
    /* Was nicht am Bus antwortet, kann nicht messen. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    CHECK(!sensor_registry_set_state(&reg, SENSOR_AS3935, SENSOR_PRODUCTIVE));
    CHECK(!sensor_registry_set_state(&reg, SENSOR_AS3935, SENSOR_TEST));
    CHECK(sensor_registry_state(&reg, SENSOR_AS3935) == SENSOR_INACTIVE);
}

TEST(test_reed_und_fahne_brauchen_keine_erkennung)
{
    /* Anemometer, Windfahne und Kippwaage haengen an GPIO bzw. ADC und lassen sich
     * nicht abfragen -- fuer sie darf die Erkennungspruefung nicht gelten. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    CHECK(sensor_registry_set_state(&reg, SENSOR_WIND_SPEED, SENSOR_PRODUCTIVE));
    CHECK(sensor_registry_set_state(&reg, SENSOR_WIND_VANE, SENSOR_PRODUCTIVE));
    CHECK(sensor_registry_set_state(&reg, SENSOR_RAIN_GAUGE, SENSOR_PRODUCTIVE));
    CHECK(sensor_registry_state(&reg, SENSOR_RAIN_GAUGE) == SENSOR_PRODUCTIVE);
}

TEST(test_abschalten_geht_immer)
{
    /* Auch einen nicht erkannten Sensor muss man auf inaktiv setzen duerfen. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    CHECK(sensor_registry_set_state(&reg, SENSOR_AS3935, SENSOR_INACTIVE));
}

TEST(test_zustaende_haengen_am_einzelnen_sensor)
{
    /* Der BME280 misst draussen, waehrend der AS3935 auf dem Tisch liegt. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_registry_set_detected(&reg, SENSOR_BME280, true);
    sensor_registry_set_detected(&reg, SENSOR_AS3935, true);
    CHECK(sensor_registry_set_state(&reg, SENSOR_BME280, SENSOR_PRODUCTIVE));
    CHECK(sensor_registry_set_state(&reg, SENSOR_AS3935, SENSOR_TEST));

    CHECK(sensor_registry_state(&reg, SENSOR_BME280) == SENSOR_PRODUCTIVE);
    CHECK(sensor_registry_state(&reg, SENSOR_AS3935) == SENSOR_TEST);
    CHECK(sensor_registry_count(&reg, SENSOR_PRODUCTIVE) == 1);
    CHECK(sensor_registry_count(&reg, SENSOR_TEST) == 1);
    CHECK(sensor_registry_count(&reg, SENSOR_INACTIVE) == SENSOR_COUNT - 2);
}

TEST(test_packen_und_entpacken_ist_verlustfrei)
{
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_state_t muster[SENSOR_COUNT] = {
        SENSOR_PRODUCTIVE, SENSOR_PRODUCTIVE, SENSOR_TEST, SENSOR_INACTIVE,
        SENSOR_TEST, SENSOR_INACTIVE, SENSOR_PRODUCTIVE, SENSOR_TEST,
    };
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        sensor_registry_set_detected(&reg, (sensor_id_t)i, true);
        sensor_registry_set_state(&reg, (sensor_id_t)i, muster[i]);
    }

    sensor_registry_t zurueck;
    sensor_registry_init(&zurueck);
    sensor_registry_unpack(&zurueck, sensor_registry_pack(&reg));
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        CHECK(sensor_registry_state(&zurueck, (sensor_id_t)i) == muster[i]);
    }
}

TEST(test_geloeschter_flash_ergibt_inaktiv)
{
    /* Ungeschriebener Flash hat alle Bits auf 1 -- das ist kein gueltiger Zustand
     * und darf nicht als produktiv durchgehen. */
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_registry_unpack(&reg, 0xFFFF);
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        CHECK(sensor_registry_state(&reg, (sensor_id_t)i) == SENSOR_INACTIVE);
    }
}

TEST(test_zwei_byte_reichen_fuer_alle_sensoren)
{
    CHECK(SENSOR_COUNT * 2 <= 16);
}

TEST(test_namen_und_kennungen_passen_zusammen)
{
    for (int i = 0; i < SENSOR_COUNT; ++i) {
        const char *key = sensor_key((sensor_id_t)i);
        CHECK(key[0] != '\0');
        CHECK(sensor_from_key(key) == (sensor_id_t)i);
    }
    CHECK(sensor_from_key("gibtsnicht") == SENSOR_COUNT);
    CHECK(sensor_from_key(NULL) == SENSOR_COUNT);
}

TEST(test_zustandsnamen_passen_zum_server)
{
    /* Muessen woertlich zu wetter/db/sensors.py passen, sonst versteht der Server
     * die Nachricht nicht und verwirft sie stillschweigend. */
    CHECK(strcmp(sensor_state_name(SENSOR_PRODUCTIVE), "productive") == 0);
    CHECK(strcmp(sensor_state_name(SENSOR_TEST), "test") == 0);
    CHECK(strcmp(sensor_state_name(SENSOR_INACTIVE), "inactive") == 0);

    sensor_state_t s;
    CHECK(sensor_state_from_name("test", &s) && s == SENSOR_TEST);
    CHECK(sensor_state_from_name("productive", &s) && s == SENSOR_PRODUCTIVE);
    CHECK(!sensor_state_from_name("vielleicht", &s));
}

TEST(test_json_enthaelt_alle_sensoren)
{
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    sensor_registry_set_detected(&reg, SENSOR_BME280, true);
    sensor_registry_set_state(&reg, SENSOR_BME280, SENSOR_PRODUCTIVE);

    char buf[512];
    int n = sensor_registry_to_json(&reg, buf, sizeof(buf));
    CHECK(n > 0);
    CHECK(strstr(buf, "\"bme280\":\"productive\"") != NULL);
    CHECK(strstr(buf, "\"as3935\":\"inactive\"") != NULL);
    /* Jeder Sensor genau einmal: acht Doppelpunkte trennen Name und Wert. */
    int paare = 0;
    for (const char *p = buf; *p; ++p) {
        if (*p == ':') {
            paare++;
        }
    }
    CHECK(paare == SENSOR_COUNT);
}

TEST(test_json_meldet_zu_kleinen_puffer)
{
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    char buf[20];
    CHECK(sensor_registry_to_json(&reg, buf, sizeof(buf)) == -1);
}

TEST(test_ungueltige_kennungen_werden_abgefangen)
{
    sensor_registry_t reg;
    sensor_registry_init(&reg);
    CHECK(!sensor_registry_set_state(&reg, SENSOR_COUNT, SENSOR_TEST));
    CHECK(!sensor_registry_set_state(&reg, (sensor_id_t)-1, SENSOR_TEST));
    CHECK(sensor_registry_state(&reg, SENSOR_COUNT) == SENSOR_INACTIVE);
    CHECK(sensor_i2c_address(SENSOR_COUNT) == 0);
    CHECK(sensor_key(SENSOR_COUNT)[0] == '\0');
}

int main(void)
{
    RUN(test_startzustand_ist_alles_inaktiv);
    RUN(test_neu_erkannter_sensor_startet_im_testmodus);
    RUN(test_bewusste_einstellung_ueberlebt_den_neustart);
    RUN(test_abgezogener_sensor_wird_inaktiv);
    RUN(test_nicht_erkannter_i2c_sensor_kann_nicht_produktiv_werden);
    RUN(test_reed_und_fahne_brauchen_keine_erkennung);
    RUN(test_abschalten_geht_immer);
    RUN(test_zustaende_haengen_am_einzelnen_sensor);
    RUN(test_packen_und_entpacken_ist_verlustfrei);
    RUN(test_geloeschter_flash_ergibt_inaktiv);
    RUN(test_zwei_byte_reichen_fuer_alle_sensoren);
    RUN(test_namen_und_kennungen_passen_zusammen);
    RUN(test_zustandsnamen_passen_zum_server);
    RUN(test_json_enthaelt_alle_sensoren);
    RUN(test_json_meldet_zu_kleinen_puffer);
    RUN(test_ungueltige_kennungen_werden_abgefangen);
    return test_summary("sensor_registry");
}
