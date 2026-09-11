/* WLAN der Aussenstation.
 *
 * Die Verbindung wird nicht dauerhaft gehalten, sondern zum Senden auf- und
 * danach wieder abgebaut. Das Funkteil ist mit Abstand der groesste Verbraucher:
 * senden zieht ueber 100 mA, der schlafende Kern unter 10. Bei einer Sendung
 * alle zwei Minuten laeuft das Funkteil damit nur wenige Prozent der Zeit.
 */
#ifndef WIFI_H
#define WIFI_H

#include <stdbool.h>

#include "esp_err.h"

esp_err_t wifi_init(const char *ssid, const char *password);

/* Verbindet und wartet, bis eine IP da ist. Gibt ESP_ERR_TIMEOUT zurueck, wenn
 * das nicht klappt -- dann bleiben die Datensaetze im Ringpuffer liegen und der
 * naechste Versuch holt sie nach. */
esp_err_t wifi_connect(int timeout_ms);
void wifi_disconnect(void);
bool wifi_connected(void);

/* Empfangsfeldstaerke in dBm, oder 0 wenn nicht verbunden. Gehoert in jede
 * Nachricht: eine Station, deren Feldstaerke ueber Wochen faellt, hat ein
 * Problem, das man sehen will, bevor sie ganz ausfaellt. */
int wifi_rssi(void);

#endif /* WIFI_H */
