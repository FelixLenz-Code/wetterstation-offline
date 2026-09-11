idf_component_register(
    SRCS "wifi.c" "mqtt_link.c" "timesync.c"
    INCLUDE_DIRS "include"
    REQUIRES esp_wifi esp_netif esp_event mqtt nvs_flash esp_timer lwip json
)
