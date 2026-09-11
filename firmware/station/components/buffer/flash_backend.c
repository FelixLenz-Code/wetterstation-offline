#include "flash_backend.h"

#include "esp_log.h"
#include "esp_partition.h"

static const char *TAG = "flash_backend";

/* Muss zum Eintrag in partitions.csv passen. */
#define PARTITION_NAME "messdaten"
#define PARTITION_SUBTYPE 0x40

static const esp_partition_t *s_part;

static int lesen(void *ctx, uint32_t offset, void *dst, size_t len)
{
    (void)ctx;
    return esp_partition_read(s_part, offset, dst, len) == ESP_OK ? 0 : -1;
}

static int schreiben(void *ctx, uint32_t offset, const void *src, size_t len)
{
    (void)ctx;
    return esp_partition_write(s_part, offset, src, len) == ESP_OK ? 0 : -1;
}

static int loeschen(void *ctx, uint32_t offset, size_t len)
{
    (void)ctx;
    return esp_partition_erase_range(s_part, offset, len) == ESP_OK ? 0 : -1;
}

esp_err_t flash_backend_init(rb_flash_t *out)
{
    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    s_part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, PARTITION_SUBTYPE,
                                      PARTITION_NAME);
    if (s_part == NULL) {
        ESP_LOGE(TAG, "Partition '%s' fehlt -- partitions.csv pruefen", PARTITION_NAME);
        return ESP_ERR_NOT_FOUND;
    }

    out->read = lesen;
    out->write = schreiben;
    out->erase = loeschen;
    out->ctx = NULL;
    out->size = s_part->size;
    out->sector_size = SPI_FLASH_SEC_SIZE;

    ESP_LOGI(TAG, "Messdatenpartition: %lu KB ab 0x%lx",
             (unsigned long)(s_part->size / 1024), (unsigned long)s_part->address);
    return ESP_OK;
}
