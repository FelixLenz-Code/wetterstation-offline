/* Speicherbasierter Ersatz fuer die Flash-Partition.
 *
 * Bildet die Eigenheiten von NOR-Flash nach, auf die sich der Ringpuffer stuetzt:
 * Schreiben kann Bits nur von 1 auf 0 ziehen, und nur ein Loeschvorgang setzt sie
 * wieder auf 1. Ein Test gegen ein simples Byte-Array wuerde genau den Fehler
 * durchgehen lassen, der auf echter Hardware auftritt.
 *
 * Zusaetzlich kann ein Stromausfall mitten im Schreiben nachgestellt werden.
 */
#ifndef FAKE_FLASH_H
#define FAKE_FLASH_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define FAKE_FLASH_SECTOR 4096u

typedef struct {
    uint8_t *data;
    uint32_t size;
    uint32_t sector_size;
    /* Nach so vielen geschriebenen Bytes bricht der naechste Schreibvorgang ab.
     * 0 heisst: kein Stromausfall. */
    uint32_t fail_after_bytes;
    uint32_t bytes_written;
    int powered;
} fake_flash_t;

static inline void fake_flash_init(fake_flash_t *f, uint32_t size)
{
    f->data = (uint8_t *)malloc(size);
    memset(f->data, 0xFF, size);
    f->size = size;
    f->sector_size = FAKE_FLASH_SECTOR;
    f->fail_after_bytes = 0;
    f->bytes_written = 0;
    f->powered = 1;
}

static inline void fake_flash_free(fake_flash_t *f)
{
    free(f->data);
    f->data = NULL;
}

static inline int fake_flash_read(void *ctx, uint32_t off, void *dst, size_t len)
{
    fake_flash_t *f = (fake_flash_t *)ctx;
    if (off + len > f->size) {
        return -1;
    }
    memcpy(dst, f->data + off, len);
    return 0;
}

static inline int fake_flash_write(void *ctx, uint32_t off, const void *src, size_t len)
{
    fake_flash_t *f = (fake_flash_t *)ctx;
    if (!f->powered || off + len > f->size) {
        return -1;
    }
    const uint8_t *s = (const uint8_t *)src;
    for (size_t i = 0; i < len; ++i) {
        if (f->fail_after_bytes && f->bytes_written >= f->fail_after_bytes) {
            /* Strom weg: der Rest des Datensatzes wird nie geschrieben. */
            f->powered = 0;
            return -1;
        }
        /* NOR-Semantik: Schreiben kann nur Bits loeschen. */
        f->data[off + i] &= s[i];
        f->bytes_written++;
    }
    return 0;
}

static inline int fake_flash_erase(void *ctx, uint32_t off, size_t len)
{
    fake_flash_t *f = (fake_flash_t *)ctx;
    if (!f->powered || off + len > f->size || off % f->sector_size != 0) {
        return -1;
    }
    memset(f->data + off, 0xFF, len);
    return 0;
}

#endif /* FAKE_FLASH_H */
