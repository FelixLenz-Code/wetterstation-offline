#include "i2c_bus.h"

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"

static const char *TAG = "i2c";

/* Ein Zugriff darf hoechstens so lange dauern. Laenger heisst: der Sensor haengt,
 * und dann ist ein Fehler die richtige Antwort -- die Station soll weitermessen,
 * was sie kann, statt auf einen kaputten Chip zu warten. */
#define I2C_TIMEOUT_MS 100

static i2c_master_bus_handle_t s_bus;
static bool s_ready;

esp_err_t i2c_bus_init(int sda, int scl)
{
    if (s_ready) {
        return ESP_OK;
    }

    i2c_master_bus_config_t cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = sda,
        .scl_io_num = scl,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        /* Interne Pull-ups reichen fuer kurze Strecken. Am Mast gehoeren
         * 4,7-kOhm-Widerstaende an die Leitung -- siehe hardware/. */
        .flags.enable_internal_pullup = true,
    };

    esp_err_t err = i2c_new_master_bus(&cfg, &s_bus);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "Bus laesst sich nicht anlegen: %s", esp_err_to_name(err));
        return err;
    }
    s_ready = true;
    ESP_LOGI(TAG, "I2C-Bus auf SDA=%d SCL=%d, %d kHz", sda, scl, I2C_BUS_FREQ_HZ / 1000);
    return ESP_OK;
}

void i2c_bus_deinit(void)
{
    if (!s_ready) {
        return;
    }
    i2c_del_master_bus(s_bus);
    s_ready = false;
}

bool i2c_bus_probe(uint8_t address)
{
    if (!s_ready) {
        return false;
    }
    return i2c_master_probe(s_bus, address, I2C_TIMEOUT_MS) == ESP_OK;
}

/* Geraetegriffe werden je Zugriff angelegt und wieder verworfen. Das kostet
 * etwas Zeit, erspart aber eine Tabelle offener Griffe -- und bei einem Lesetakt
 * von zehn Sekunden faellt es nicht ins Gewicht. */
static esp_err_t mit_geraet(uint8_t address, i2c_master_dev_handle_t *dev)
{
    i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = I2C_BUS_FREQ_HZ,
    };
    return i2c_master_bus_add_device(s_bus, &cfg, dev);
}

esp_err_t i2c_bus_read(uint8_t address, uint8_t reg, uint8_t *out, size_t len)
{
    if (!s_ready || out == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }

    i2c_master_dev_handle_t dev;
    esp_err_t err = mit_geraet(address, &dev);
    if (err != ESP_OK) {
        return err;
    }

    err = i2c_master_transmit_receive(dev, &reg, 1, out, len, I2C_TIMEOUT_MS);
    i2c_master_bus_rm_device(dev);
    return err;
}

esp_err_t i2c_bus_write(uint8_t address, uint8_t reg, uint8_t value)
{
    if (!s_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    i2c_master_dev_handle_t dev;
    esp_err_t err = mit_geraet(address, &dev);
    if (err != ESP_OK) {
        return err;
    }

    const uint8_t puffer[2] = {reg, value};
    err = i2c_master_transmit(dev, puffer, sizeof(puffer), I2C_TIMEOUT_MS);
    i2c_master_bus_rm_device(dev);
    return err;
}
