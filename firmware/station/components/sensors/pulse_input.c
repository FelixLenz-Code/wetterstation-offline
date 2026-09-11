#include "pulse_input.h"

#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char *TAG = "pulse";

typedef struct {
    volatile uint32_t count;
    volatile uint32_t rejected;
    volatile int64_t last_us;
    uint32_t debounce_us;
    int gpio;
    bool ready;
} kanal_t;

static kanal_t s_kanal[PULSE_CHANNELS];

/* Im IRAM, damit der Interrupt auch dann bedient wird, wenn der Flash gerade
 * beschrieben wird -- sonst ginge beim Wegschreiben eines Datensatzes genau der
 * Impuls verloren, der die Boee ausmacht. */
static void IRAM_ATTR impuls(void *arg)
{
    kanal_t *k = (kanal_t *)arg;
    const int64_t jetzt = esp_timer_get_time();

    if (jetzt - k->last_us < (int64_t)k->debounce_us) {
        k->rejected++;
        return;
    }
    k->last_us = jetzt;
    k->count++;
}

esp_err_t pulse_input_init(pulse_channel_t channel, int gpio, uint32_t debounce_us)
{
    if (channel < 0 || channel >= PULSE_CHANNELS) {
        return ESP_ERR_INVALID_ARG;
    }

    kanal_t *k = &s_kanal[channel];
    k->count = 0;
    k->rejected = 0;
    k->last_us = 0;
    k->debounce_us = debounce_us;
    k->gpio = gpio;

    const gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << gpio,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        /* Auf die fallende Flanke: der Reed-Kontakt zieht gegen Masse. */
        .intr_type = GPIO_INTR_NEGEDGE,
    };
    esp_err_t err = gpio_config(&cfg);
    if (err != ESP_OK) {
        return err;
    }

    /* Der Interrupt muss den Kern aus dem Light-Sleep holen koennen -- sonst
     * zaehlt er nur, solange die Station ohnehin wach ist. */
    err = gpio_wakeup_enable(gpio, GPIO_INTR_LOW_LEVEL);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "GPIO %d weckt nicht aus dem Light-Sleep: %s", gpio,
                 esp_err_to_name(err));
    }

    err = gpio_isr_handler_add(gpio, impuls, k);
    if (err != ESP_OK) {
        return err;
    }

    k->ready = true;
    ESP_LOGI(TAG, "Kanal %d auf GPIO %d, Entprellung %lu us", (int)channel, gpio,
             (unsigned long)debounce_us);
    return ESP_OK;
}

uint32_t pulse_input_take(pulse_channel_t channel)
{
    if (channel < 0 || channel >= PULSE_CHANNELS || !s_kanal[channel].ready) {
        return 0;
    }
    /* Interrupts kurz aus: zwischen Lesen und Nullsetzen darf kein Impuls
     * hineinfallen, sonst zaehlt er in keinem der beiden Zeitraeume. */
    portDISABLE_INTERRUPTS();
    const uint32_t n = s_kanal[channel].count;
    s_kanal[channel].count = 0;
    portENABLE_INTERRUPTS();
    return n;
}

uint32_t pulse_input_peek(pulse_channel_t channel)
{
    if (channel < 0 || channel >= PULSE_CHANNELS) {
        return 0;
    }
    return s_kanal[channel].count;
}

uint32_t pulse_input_rejected(pulse_channel_t channel)
{
    if (channel < 0 || channel >= PULSE_CHANNELS) {
        return 0;
    }
    return s_kanal[channel].rejected;
}
