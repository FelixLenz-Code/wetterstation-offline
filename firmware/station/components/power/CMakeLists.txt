idf_component_register(
    SRCS "power.c" "power_policy.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_adc esp_pm driver
)
