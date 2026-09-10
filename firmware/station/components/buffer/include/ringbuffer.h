/* Append-Only-Ringpuffer fuer Messdatensaetze in einer Flash-Partition.
 *
 * Zweck: kein Messwert geht verloren, wenn WLAN oder Server ausfallen. Datensaetze
 * werden geschrieben und erst dann freigegeben, wenn der Broker sie per PUBACK
 * bestaetigt hat. Bei 1 MB Partition und 32 Byte je Datensatz sind das rund zwei
 * Wochen Ausfall im Minutentakt.
 *
 * Warum kein einfacher Schreibzeiger im NVS: der muesste bei jeder Bestaetigung
 * neu geschrieben werden, also bei zweiminuetigen Sendungen rund 260.000-mal im
 * Jahr. Flash haelt etwa 100.000 Loeschzyklen je Sektor aus -- der Zeiger waere
 * nach wenigen Monaten der erste Ausfall.
 *
 * Stattdessen traegt jeder Platz ein Zustandsbyte, das nur *Bits loescht*:
 *
 *     0xFF  geloescht, frei
 *     0xFE  beschrieben, noch nicht bestaetigt
 *     0x00  bestaetigt, darf ueberschrieben werden
 *
 * Der Uebergang 0xFF -> 0xFE -> 0x00 loescht ausschliesslich Bits und kommt damit
 * ohne Loeschvorgang aus. Erst wenn ein ganzer Sektor bestaetigt ist, wird er am
 * Stueck geloescht. Der Zustand steht also im Puffer selbst -- ein Stromausfall
 * mitten im Senden kann hoechstens dazu fuehren, dass ein bereits zugestellter
 * Datensatz noch einmal kommt, nie dazu, dass einer verschwindet.
 */
#ifndef RINGBUFFER_H
#define RINGBUFFER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define RB_STATE_FREE 0xFFu
#define RB_STATE_WRITTEN 0xFEu
#define RB_STATE_ACKED 0x00u

/* Groesste Nutzlast je Datensatz. */
#define RB_MAX_PAYLOAD 64

typedef enum {
    RB_OK = 0,
    RB_ERR_ARG = -1,     /* unsinnige Parameter */
    RB_ERR_IO = -2,      /* der Flash-Treiber meldete einen Fehler */
    RB_ERR_EMPTY = -3,   /* nichts Unbestaetigtes vorhanden */
    RB_ERR_TOO_BIG = -4, /* Nutzlast passt nicht in einen Platz */
} rb_err_t;

/* Zugriff auf den Speicher. Auf dem ESP32 die Flash-Partition, im Test ein
 * Speicherabbild -- dadurch laesst sich die gesamte Logik auf dem Rechner pruefen. */
typedef struct {
    /* Liest len Bytes ab offset. Rueckgabe 0 bei Erfolg. */
    int (*read)(void *ctx, uint32_t offset, void *dst, size_t len);
    /* Schreibt len Bytes ab offset. Darf nur Bits von 1 auf 0 setzen. */
    int (*write)(void *ctx, uint32_t offset, const void *src, size_t len);
    /* Loescht einen Sektor (setzt alle Bytes auf 0xFF). */
    int (*erase)(void *ctx, uint32_t offset, size_t len);
    void *ctx;
    uint32_t size;        /* Gesamtgroesse in Byte */
    uint32_t sector_size; /* Loeschgranularitaet, auf dem ESP32 4096 */
} rb_flash_t;

typedef struct {
    rb_flash_t flash;
    uint32_t slot_size;    /* Bytes je Platz, inkl. Kopf */
    uint32_t slots;        /* Anzahl Plaetze insgesamt */
    uint32_t slots_per_sector;
    uint32_t head;         /* naechster zu beschreibender Platz */
    uint32_t tail;         /* aeltester unbestaetigter Platz */
    uint32_t next_seq;     /* naechste Sequenznummer */
    uint32_t dropped;      /* verworfene Datensaetze, seit der Puffer lief */
    bool initialised;
} rb_t;

/* Ein gelesener Datensatz. */
typedef struct {
    uint32_t seq;
    uint32_t slot;
    uint8_t len;
    uint8_t payload[RB_MAX_PAYLOAD];
} rb_record_t;

/* Richtet den Puffer ein und stellt den Zustand aus dem Flash wieder her.
 * payload_size ist die feste Nutzlastgroesse (<= RB_MAX_PAYLOAD). */
rb_err_t rb_init(rb_t *rb, const rb_flash_t *flash, uint8_t payload_size);

/* Haengt einen Datensatz an. Ist der Puffer voll, wird der aelteste Sektor
 * geloescht und dropped erhoeht -- laufende Messung geht vor alten Daten. */
rb_err_t rb_append(rb_t *rb, const void *payload, uint8_t len, uint32_t *seq_out);

/* Liest bis zu max Datensaetze ab dem aeltesten unbestaetigten, ohne sie zu
 * veraendern. Gibt die Anzahl in count_out zurueck. */
rb_err_t rb_peek(rb_t *rb, rb_record_t *out, uint32_t max, uint32_t *count_out);

/* Bestaetigt alle Datensaetze bis einschliesslich seq. */
rb_err_t rb_ack_through(rb_t *rb, uint32_t seq);

/* Anzahl unbestaetigter Datensaetze. */
uint32_t rb_pending(const rb_t *rb);

/* Loescht den gesamten Puffer. */
rb_err_t rb_reset(rb_t *rb);

/* CRC-16/CCITT-FALSE -- exportiert, damit der Test denselben Wert pruefen kann. */
uint16_t rb_crc16(const void *data, size_t len);

#endif /* RINGBUFFER_H */
