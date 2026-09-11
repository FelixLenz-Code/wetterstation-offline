idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES sensors buffer link power nvs_flash esp_timer
)
