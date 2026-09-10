#include "ringbuffer.h"

#include <string.h>

/* Kopf jedes Platzes. Reihenfolge ist wichtig: state steht vorn, damit es sich
 * spaeter einzeln auf 0x00 ziehen laesst, ohne den Rest anzufassen. */
typedef struct __attribute__((packed)) {
    uint8_t state;
    uint8_t len;
    uint16_t crc;
    uint32_t seq;
} rb_header_t;

#define RB_HEADER_SIZE ((uint32_t)sizeof(rb_header_t))

uint16_t rb_crc16(const void *data, size_t len)
{
    const uint8_t *p = (const uint8_t *)data;
    uint16_t crc = 0xFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= (uint16_t)p[i] << 8;
        for (int b = 0; b < 8; ++b) {
            crc = (crc & 0x8000u) ? (uint16_t)((crc << 1) ^ 0x1021u) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static uint32_t slot_offset(const rb_t *rb, uint32_t slot)
{
    return slot * rb->slot_size;
}

static int read_header(rb_t *rb, uint32_t slot, rb_header_t *hdr)
{
    return rb->flash.read(rb->flash.ctx, slot_offset(rb, slot), hdr, RB_HEADER_SIZE);
}

/* Ein Platz gilt nur als gueltig, wenn Zustand *und* Pruefsumme stimmen. Ein
 * halb geschriebener Datensatz nach Stromausfall faellt damit durch. */
static bool slot_valid(rb_t *rb, uint32_t slot, rb_header_t *hdr, uint8_t *payload)
{
    if (read_header(rb, slot, hdr) != 0) {
        return false;
    }
    if (hdr->state != RB_STATE_WRITTEN && hdr->state != RB_STATE_ACKED) {
        return false;
    }
    if (hdr->len == 0 || hdr->len > RB_MAX_PAYLOAD ||
        hdr->len > rb->slot_size - RB_HEADER_SIZE) {
        return false;
    }
    uint8_t local[RB_MAX_PAYLOAD];
    uint8_t *dst = payload ? payload : local;
    if (rb->flash.read(rb->flash.ctx, slot_offset(rb, slot) + RB_HEADER_SIZE, dst,
                       hdr->len) != 0) {
        return false;
    }
    return rb_crc16(dst, hdr->len) == hdr->crc;
}

static rb_err_t erase_sector_of(rb_t *rb, uint32_t slot)
{
    uint32_t sector = (slot / rb->slots_per_sector) * rb->slots_per_sector;
    uint32_t offset = slot_offset(rb, sector);
    if (rb->flash.erase(rb->flash.ctx, offset, rb->flash.sector_size) != 0) {
        return RB_ERR_IO;
    }
    return RB_OK;
}

rb_err_t rb_init(rb_t *rb, const rb_flash_t *flash, uint8_t payload_size)
{
    if (rb == NULL || flash == NULL || payload_size == 0 ||
        payload_size > RB_MAX_PAYLOAD) {
        return RB_ERR_ARG;
    }
    if (flash->read == NULL || flash->write == NULL || flash->erase == NULL) {
        return RB_ERR_ARG;
    }
    if (flash->sector_size == 0 || flash->size < flash->sector_size) {
        return RB_ERR_ARG;
    }

    memset(rb, 0, sizeof(*rb));
    rb->flash = *flash;
    rb->slot_size = RB_HEADER_SIZE + payload_size;

    /* Plaetze duerfen keine Sektorgrenze ueberschreiten -- sonst wuerde ein
     * Loeschvorgang einen Datensatz halbieren. Deshalb wird aufgerundet, bis die
     * Sektorgroesse glatt aufgeht. */
    rb->slots_per_sector = flash->sector_size / rb->slot_size;
    if (rb->slots_per_sector == 0) {
        return RB_ERR_ARG;
    }
    rb->slot_size = flash->sector_size / rb->slots_per_sector;
    rb->slots = (flash->size / flash->sector_size) * rb->slots_per_sector;
    if (rb->slots < 2) {
        return RB_ERR_ARG;
    }

    /* Zustand aus dem Flash rekonstruieren: hoechste und niedrigste Sequenznummer
     * suchen. Der Puffer ist ein Ring, die Nummern laufen also im Kreis -- deshalb
     * wird die groesste Luecke gesucht statt schlicht das Minimum. */
    rb_header_t hdr;
    uint32_t max_seq = 0;
    uint32_t max_slot = 0;
    bool any = false;
    uint32_t oldest_pending_seq = 0;
    uint32_t oldest_pending_slot = 0;
    bool any_pending = false;

    for (uint32_t s = 0; s < rb->slots; ++s) {
        if (!slot_valid(rb, s, &hdr, NULL)) {
            continue;
        }
        if (!any || (int32_t)(hdr.seq - max_seq) > 0) {
            max_seq = hdr.seq;
            max_slot = s;
            any = true;
        }
        if (hdr.state == RB_STATE_WRITTEN) {
            if (!any_pending || (int32_t)(hdr.seq - oldest_pending_seq) < 0) {
                oldest_pending_seq = hdr.seq;
                oldest_pending_slot = s;
                any_pending = true;
            }
        }
    }

    if (!any) {
        rb->head = 0;
        rb->tail = 0;
        rb->next_seq = 1;
    } else {
        rb->head = (max_slot + 1) % rb->slots;
        rb->next_seq = max_seq + 1;
        rb->tail = any_pending ? oldest_pending_slot : rb->head;
    }
    rb->initialised = true;
    return RB_OK;
}

rb_err_t rb_append(rb_t *rb, const void *payload, uint8_t len, uint32_t *seq_out)
{
    if (rb == NULL || !rb->initialised || payload == NULL || len == 0) {
        return RB_ERR_ARG;
    }
    if (len > rb->slot_size - RB_HEADER_SIZE) {
        return RB_ERR_TOO_BIG;
    }

    rb_header_t hdr;
    /* Ist der Zielplatz noch belegt, muss sein Sektor weichen. Enthaelt der noch
     * Unbestaetigtes, gehen diese Datensaetze verloren -- bewusst: eine laufende
     * Messung ist mehr wert als zwei Wochen alte Werte, und der Verlust wird in
     * dropped gezaehlt statt stillschweigend hingenommen. */
    if (slot_valid(rb, rb->head, &hdr, NULL)) {
        uint32_t sector_start = (rb->head / rb->slots_per_sector) * rb->slots_per_sector;
        for (uint32_t i = 0; i < rb->slots_per_sector; ++i) {
            rb_header_t alt;
            uint32_t s = sector_start + i;
            if (slot_valid(rb, s, &alt, NULL) && alt.state == RB_STATE_WRITTEN) {
                rb->dropped++;
            }
        }
        rb_err_t err = erase_sector_of(rb, rb->head);
        if (err != RB_OK) {
            return err;
        }
        rb->head = sector_start;
        /* Zeigte tail in den geloeschten Sektor, ruecken beide zusammen. */
        uint32_t tail_sector = (rb->tail / rb->slots_per_sector) * rb->slots_per_sector;
        if (tail_sector == sector_start) {
            rb->tail = (sector_start + rb->slots_per_sector) % rb->slots;
        }
    }

    uint8_t block[RB_MAX_PAYLOAD + sizeof(rb_header_t)];
    rb_header_t *out = (rb_header_t *)block;
    out->state = RB_STATE_WRITTEN;
    out->len = len;
    out->crc = rb_crc16(payload, len);
    out->seq = rb->next_seq;
    memcpy(block + RB_HEADER_SIZE, payload, len);

    if (rb->flash.write(rb->flash.ctx, slot_offset(rb, rb->head), block,
                        RB_HEADER_SIZE + len) != 0) {
        return RB_ERR_IO;
    }

    if (rb_pending(rb) == 0) {
        rb->tail = rb->head;
    }
    if (seq_out != NULL) {
        *seq_out = rb->next_seq;
    }
    rb->next_seq++;
    rb->head = (rb->head + 1) % rb->slots;
    return RB_OK;
}

rb_err_t rb_peek(rb_t *rb, rb_record_t *out, uint32_t max, uint32_t *count_out)
{
    if (rb == NULL || !rb->initialised || out == NULL || count_out == NULL) {
        return RB_ERR_ARG;
    }
    *count_out = 0;
    rb_header_t hdr;
    for (uint32_t i = 0; i < rb->slots && *count_out < max; ++i) {
        uint32_t s = (rb->tail + i) % rb->slots;
        uint8_t payload[RB_MAX_PAYLOAD];
        if (!slot_valid(rb, s, &hdr, payload) || hdr.state != RB_STATE_WRITTEN) {
            continue;
        }
        rb_record_t *rec = &out[*count_out];
        rec->seq = hdr.seq;
        rec->slot = s;
        rec->len = hdr.len;
        memcpy(rec->payload, payload, hdr.len);
        (*count_out)++;
    }
    return (*count_out > 0) ? RB_OK : RB_ERR_EMPTY;
}

rb_err_t rb_ack_through(rb_t *rb, uint32_t seq)
{
    if (rb == NULL || !rb->initialised) {
        return RB_ERR_ARG;
    }
    rb_header_t hdr;
    const uint8_t acked = RB_STATE_ACKED;
    for (uint32_t s = 0; s < rb->slots; ++s) {
        if (!slot_valid(rb, s, &hdr, NULL) || hdr.state != RB_STATE_WRITTEN) {
            continue;
        }
        if ((int32_t)(hdr.seq - seq) > 0) {
            continue;
        }
        /* Nur das Zustandsbyte anfassen: 0xFE -> 0x00 loescht ausschliesslich Bits
         * und braucht deshalb keinen Loeschvorgang. */
        if (rb->flash.write(rb->flash.ctx, slot_offset(rb, s), &acked, 1) != 0) {
            return RB_ERR_IO;
        }
    }

    /* tail auf den naechsten unbestaetigten Platz nachziehen. */
    for (uint32_t i = 0; i < rb->slots; ++i) {
        uint32_t s = (rb->tail + i) % rb->slots;
        if (slot_valid(rb, s, &hdr, NULL) && hdr.state == RB_STATE_WRITTEN) {
            rb->tail = s;
            return RB_OK;
        }
    }
    rb->tail = rb->head;
    return RB_OK;
}

uint32_t rb_pending(const rb_t *rb)
{
    if (rb == NULL || !rb->initialised) {
        return 0;
    }
    rb_t *mut = (rb_t *)rb;
    rb_header_t hdr;
    uint32_t n = 0;
    for (uint32_t s = 0; s < rb->slots; ++s) {
        if (slot_valid(mut, s, &hdr, NULL) && hdr.state == RB_STATE_WRITTEN) {
            n++;
        }
    }
    return n;
}

rb_err_t rb_reset(rb_t *rb)
{
    if (rb == NULL || !rb->initialised) {
        return RB_ERR_ARG;
    }
    for (uint32_t off = 0; off < rb->flash.size; off += rb->flash.sector_size) {
        if (rb->flash.erase(rb->flash.ctx, off, rb->flash.sector_size) != 0) {
            return RB_ERR_IO;
        }
    }
    rb->head = 0;
    rb->tail = 0;
    rb->next_seq = 1;
    rb->dropped = 0;
    return RB_OK;
}
