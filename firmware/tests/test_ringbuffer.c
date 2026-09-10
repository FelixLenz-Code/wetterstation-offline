/* Tests des Store-and-Forward-Ringpuffers.
 *
 * Der wichtigste Test ist test_stromausfall_mitten_im_schreiben: genau dort
 * entscheidet sich, ob der Offline-Anspruch traegt oder nur behauptet ist.
 */
#include "fake_flash.h"
#include "ringbuffer.h"
#include "test_util.h"

#define PAYLOAD 28u

static fake_flash_t g_flash;

static void setup(rb_t *rb, uint32_t size)
{
    fake_flash_free(&g_flash);
    fake_flash_init(&g_flash, size);
    rb_flash_t f = {
        .read = fake_flash_read,
        .write = fake_flash_write,
        .erase = fake_flash_erase,
        .ctx = &g_flash,
        .size = size,
        .sector_size = FAKE_FLASH_SECTOR,
    };
    CHECK(rb_init(rb, &f, PAYLOAD) == RB_OK);
}

static void fill_payload(uint8_t *buf, uint8_t marker)
{
    for (uint32_t i = 0; i < PAYLOAD; ++i) {
        buf[i] = (uint8_t)(marker + i);
    }
}

TEST(test_crc_erkennt_aenderung)
{
    uint8_t a[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    uint8_t b[8] = {1, 2, 3, 4, 5, 6, 7, 9};
    CHECK(rb_crc16(a, 8) != rb_crc16(b, 8));
    CHECK(rb_crc16(a, 8) == rb_crc16(a, 8));
}

TEST(test_anhaengen_und_lesen)
{
    rb_t rb;
    setup(&rb, 4 * FAKE_FLASH_SECTOR);

    uint8_t buf[PAYLOAD];
    for (int i = 0; i < 5; ++i) {
        fill_payload(buf, (uint8_t)(10 * i));
        uint32_t seq = 0;
        CHECK(rb_append(&rb, buf, PAYLOAD, &seq) == RB_OK);
        CHECK(seq == (uint32_t)(i + 1));
    }
    CHECK(rb_pending(&rb) == 5);

    rb_record_t recs[10];
    uint32_t n = 0;
    CHECK(rb_peek(&rb, recs, 10, &n) == RB_OK);
    CHECK(n == 5);
    for (int i = 0; i < 5; ++i) {
        CHECK(recs[i].seq == (uint32_t)(i + 1));
        fill_payload(buf, (uint8_t)(10 * i));
        CHECK(memcmp(recs[i].payload, buf, PAYLOAD) == 0);
    }
}

TEST(test_bestaetigen_gibt_plaetze_frei)
{
    rb_t rb;
    setup(&rb, 4 * FAKE_FLASH_SECTOR);
    uint8_t buf[PAYLOAD];
    for (int i = 0; i < 6; ++i) {
        fill_payload(buf, (uint8_t)i);
        CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_OK);
    }
    CHECK(rb_pending(&rb) == 6);
    CHECK(rb_ack_through(&rb, 4) == RB_OK);
    CHECK(rb_pending(&rb) == 2);

    rb_record_t recs[10];
    uint32_t n = 0;
    CHECK(rb_peek(&rb, recs, 10, &n) == RB_OK);
    CHECK(n == 2);
    CHECK(recs[0].seq == 5);
    CHECK(recs[1].seq == 6);
}

TEST(test_bestaetigen_braucht_keinen_loeschvorgang)
{
    /* Der ganze Sinn des Zustandsbytes: eine Bestaetigung darf den Flash nicht
     * abnutzen. Geprueft wird, dass sich ausser dem Zustandsbyte nichts aendert. */
    rb_t rb;
    setup(&rb, 2 * FAKE_FLASH_SECTOR);
    uint8_t buf[PAYLOAD];
    fill_payload(buf, 42);
    CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_OK);

    uint8_t vorher[64];
    memcpy(vorher, g_flash.data, sizeof(vorher));
    CHECK(rb_ack_through(&rb, 1) == RB_OK);

    CHECK(g_flash.data[0] == RB_STATE_ACKED);
    CHECK(vorher[0] == RB_STATE_WRITTEN);
    /* Alles hinter dem Zustandsbyte ist unveraendert. */
    CHECK(memcmp(vorher + 1, g_flash.data + 1, sizeof(vorher) - 1) == 0);
}

TEST(test_zustand_ueberlebt_neustart)
{
    rb_t rb;
    setup(&rb, 4 * FAKE_FLASH_SECTOR);
    uint8_t buf[PAYLOAD];
    for (int i = 0; i < 7; ++i) {
        fill_payload(buf, (uint8_t)i);
        CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_OK);
    }
    CHECK(rb_ack_through(&rb, 3) == RB_OK);

    /* Neustart: derselbe Flash, frischer Puffer. */
    rb_t neu;
    rb_flash_t f = {
        .read = fake_flash_read,
        .write = fake_flash_write,
        .erase = fake_flash_erase,
        .ctx = &g_flash,
        .size = g_flash.size,
        .sector_size = FAKE_FLASH_SECTOR,
    };
    CHECK(rb_init(&neu, &f, PAYLOAD) == RB_OK);
    CHECK(rb_pending(&neu) == 4);
    CHECK(neu.next_seq == 8);

    rb_record_t recs[10];
    uint32_t n = 0;
    CHECK(rb_peek(&neu, recs, 10, &n) == RB_OK);
    CHECK(n == 4);
    CHECK(recs[0].seq == 4);
}

TEST(test_stromausfall_mitten_im_schreiben)
{
    /* Die entscheidende Zusicherung: ein halb geschriebener Datensatz darf beim
     * Neustart nicht als gueltig durchgehen -- er haette eine falsche Nutzlast.
     * Alle vollstaendig geschriebenen Datensaetze muessen dagegen erhalten sein. */
    rb_t rb;
    setup(&rb, 4 * FAKE_FLASH_SECTOR);
    uint8_t buf[PAYLOAD];
    for (int i = 0; i < 3; ++i) {
        fill_payload(buf, (uint8_t)i);
        CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_OK);
    }

    /* Strom faellt nach den ersten Bytes des vierten Datensatzes aus. */
    g_flash.fail_after_bytes = g_flash.bytes_written + 6;
    fill_payload(buf, 99);
    CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_ERR_IO);

    g_flash.powered = 1;
    g_flash.fail_after_bytes = 0;

    rb_t neu;
    rb_flash_t f = {
        .read = fake_flash_read,
        .write = fake_flash_write,
        .erase = fake_flash_erase,
        .ctx = &g_flash,
        .size = g_flash.size,
        .sector_size = FAKE_FLASH_SECTOR,
    };
    CHECK(rb_init(&neu, &f, PAYLOAD) == RB_OK);

    /* Genau die drei vollstaendigen Datensaetze, kein Bruchstueck. */
    CHECK(rb_pending(&neu) == 3);
    rb_record_t recs[10];
    uint32_t n = 0;
    CHECK(rb_peek(&neu, recs, 10, &n) == RB_OK);
    CHECK(n == 3);
    for (int i = 0; i < 3; ++i) {
        CHECK(recs[i].seq == (uint32_t)(i + 1));
        fill_payload(buf, (uint8_t)i);
        CHECK(memcmp(recs[i].payload, buf, PAYLOAD) == 0);
    }
}

TEST(test_ueberlauf_verwirft_aelteste_und_zaehlt_mit)
{
    rb_t rb;
    setup(&rb, 2 * FAKE_FLASH_SECTOR);
    uint8_t buf[PAYLOAD];

    uint32_t kapazitaet = rb.slots;
    CHECK(kapazitaet > 4);

    /* Deutlich mehr schreiben, als hineinpasst -- ohne je zu bestaetigen. */
    for (uint32_t i = 0; i < kapazitaet + rb.slots_per_sector; ++i) {
        fill_payload(buf, (uint8_t)i);
        CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_OK);
    }

    /* Die Messung laeuft weiter, der Verlust wird gezaehlt statt verschwiegen. */
    CHECK(rb.dropped > 0);
    CHECK(rb_pending(&rb) > 0);
    CHECK(rb_pending(&rb) <= kapazitaet);

    /* Und der juengste Datensatz ist auf jeden Fall da. */
    rb_record_t recs[512];
    uint32_t n = 0;
    CHECK(rb_peek(&rb, recs, 512, &n) == RB_OK);
    uint32_t max_seq = 0;
    for (uint32_t i = 0; i < n; ++i) {
        if (recs[i].seq > max_seq) {
            max_seq = recs[i].seq;
        }
    }
    CHECK(max_seq == rb.next_seq - 1);
}

TEST(test_zuruecksetzen_leert_alles)
{
    rb_t rb;
    setup(&rb, 2 * FAKE_FLASH_SECTOR);
    uint8_t buf[PAYLOAD];
    fill_payload(buf, 1);
    CHECK(rb_append(&rb, buf, PAYLOAD, NULL) == RB_OK);
    CHECK(rb_reset(&rb) == RB_OK);
    CHECK(rb_pending(&rb) == 0);
    CHECK(rb.next_seq == 1);
}

TEST(test_zu_grosse_nutzlast_wird_abgelehnt)
{
    rb_t rb;
    setup(&rb, 2 * FAKE_FLASH_SECTOR);
    uint8_t buf[RB_MAX_PAYLOAD];
    memset(buf, 7, sizeof(buf));
    CHECK(rb_append(&rb, buf, RB_MAX_PAYLOAD, NULL) == RB_ERR_TOO_BIG);
}

int main(void)
{
    fake_flash_init(&g_flash, FAKE_FLASH_SECTOR);
    RUN(test_crc_erkennt_aenderung);
    RUN(test_anhaengen_und_lesen);
    RUN(test_bestaetigen_gibt_plaetze_frei);
    RUN(test_bestaetigen_braucht_keinen_loeschvorgang);
    RUN(test_zustand_ueberlebt_neustart);
    RUN(test_stromausfall_mitten_im_schreiben);
    RUN(test_ueberlauf_verwirft_aelteste_und_zaehlt_mit);
    RUN(test_zuruecksetzen_leert_alles);
    RUN(test_zu_grosse_nutzlast_wird_abgelehnt);
    fake_flash_free(&g_flash);
    return test_summary("ringbuffer");
}
