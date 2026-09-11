# Host-Tests der reinen Rechenlogik -- laufen ohne ESP-IDF und ohne Hardware.
#
# Alles, was hier getestet wird, ist bewusst frei von ESP-Abhaengigkeiten:
# Ringpuffer, Windfahnen-Kennlinie, Sensorverwaltung und die Kompensationsrechnung
# des BME280. Genau das sind die Teile, in denen sich Fehler still verstecken --
# eine um 22,5 Grad verdrehte Windrichtung oder eine um zehn Prozent danebenliegende
# Feuchte faellt am Geraet niemandem auf, hier schon.
#
# Neuen Test hinzufuegen: Namen an TESTS anhaengen und die Quellen unter
# SRC_<name> eintragen. Die Regel unten baut daraus automatisch.

CC      ?= gcc
CFLAGS  := -std=c11 -Wall -Wextra -Werror -O2 -g
CFLAGS  += -I. -I../station/components/buffer/include -I../station/components/sensors/include
LDLIBS  := -lm

SRC_DIR := ../station/components
BIN     := build

TESTS := ringbuffer wind_vane sensor_registry bme280 pulse_convert

SRC_ringbuffer      := $(SRC_DIR)/buffer/ringbuffer.c
SRC_wind_vane       := $(SRC_DIR)/sensors/wind_vane.c
SRC_sensor_registry := $(SRC_DIR)/sensors/sensor_registry.c
SRC_bme280          := $(SRC_DIR)/sensors/bme280_compensate.c
SRC_pulse_convert   := $(SRC_DIR)/sensors/pulse_convert.c

BINS := $(addprefix $(BIN)/test_,$(TESTS))

.PHONY: all check clean
all: $(BINS)

check: $(BINS)
	@fail=0; for t in $(BINS); do ./$$t || fail=1; done; exit $$fail

$(BIN):
	@mkdir -p $(BIN)

# Jeder Test besteht aus seiner eigenen Datei plus den Quellen aus SRC_<name>.
# Die zweite Expansion ist noetig, damit $* im Variablennamen ankommt.
.SECONDEXPANSION:
$(BIN)/test_%: test_%.c $$(SRC_%) | $(BIN)
	$(CC) $(CFLAGS) -o $@ $^ $(LDLIBS)

clean:
	rm -rf $(BIN)
