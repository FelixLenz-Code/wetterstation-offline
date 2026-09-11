# Die Quellen ohne ESP-Abhaengigkeit (wind_vane, sensor_registry,
# bme280_compensate) werden zusaetzlich von den Host-Tests uebersetzt --
# siehe firmware/tests. Sie duerfen deshalb nichts aus dem SDK einbinden.
idf_component_register(
    SRCS
        "wind_vane.c"
        "sensor_registry.c"
        "bme280_compensate.c"
        "i2c_bus.c"
        "bme280.c"
        "sensor_store.c"
        "pulse_input.c"
        "pulse_convert.c"
    INCLUDE_DIRS "include"
    REQUIRES driver esp_adc nvs_flash esp_timer
)
