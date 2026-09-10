# Host-Tests der reinen Rechenlogik -- laufen ohne ESP-IDF und ohne Hardware.
#
# Alles, was hier getestet wird, ist bewusst frei von ESP-Abhaengigkeiten:
# Ringpuffer, Windfahnen-Kennlinie und die meteorologischen Umrechnungen. Genau
# das sind die Teile, in denen sich Fehler still verstecken -- ein falscher
# Taupunkt faellt am Geraet niemandem auf, hier schon.

CC      ?= gcc
CFLAGS  := -std=c11 -Wall -Wextra -Werror -O2 -g
CFLAGS  += -I. -I../station/components/buffer/include -I../station/components/sensors/include
LDLIBS  := -lm

SRC_DIR := ../station/components
BIN     := build

TESTS := $(BIN)/test_ringbuffer $(BIN)/test_wind_vane

.PHONY: all check clean
all: $(TESTS)

check: $(TESTS)
	@fail=0; for t in $(TESTS); do ./$$t || fail=1; done; exit $$fail

$(BIN):
	@mkdir -p $(BIN)

$(BIN)/test_ringbuffer: test_ringbuffer.c $(SRC_DIR)/buffer/ringbuffer.c | $(BIN)
	$(CC) $(CFLAGS) -o $@ $^ $(LDLIBS)

$(BIN)/test_wind_vane: test_wind_vane.c $(SRC_DIR)/sensors/wind_vane.c | $(BIN)
	$(CC) $(CFLAGS) -o $@ $^ $(LDLIBS)

clean:
	rm -rf $(BIN)
