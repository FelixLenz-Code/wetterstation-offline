# ringbuffer.c ist frei von ESP-Abhaengigkeiten und wird von den Host-Tests
# mituebersetzt; flash_backend.c bindet ihn an die Partition an.
idf_component_register(
    SRCS "ringbuffer.c" "flash_backend.c"
    INCLUDE_DIRS "include"
    REQUIRES spi_flash esp_partition
)
