/* Zugangsdaten der Station.
 *
 * Diese Datei gehoert NICHT ins Repository -- sie steht in .gitignore. Die Werte
 * liefert `./install.sh station` auf dem Server, samt der richtigen IP.
 *
 * Zum Anlegen: config.example.h kopieren und ausfuellen.
 */
#ifndef CONFIG_H
#define CONFIG_H

#define WIFI_SSID "DEIN-WLAN"
#define WIFI_PASSWORD "DEIN-WLAN-PASSWORT"

#define MQTT_HOST "192.168.1.10"
#define MQTT_PORT 1883
#define MQTT_USER "station"
#define MQTT_PASSWORD "AUS-DER-ENV-DATEI"

/* Muss zum Namen in der Datenbank passen. */
#define STATION_KEY "garten"

/* Der Zeitabgleich laeuft gegen den eigenen Server, nicht gegen einen
 * Zeitserver im Internet -- das passt zum Offline-Anspruch. */
#define SNTP_SERVER MQTT_HOST

/* --- Pinbelegung --- */
#define PIN_I2C_SDA 21
#define PIN_I2C_SCL 22
#define PIN_WIND_PULSE 25
#define PIN_RAIN_PULSE 26
#define PIN_WIND_VANE_ADC 34  /* nur ADC1 funktioniert bei aktivem WLAN */
#define PIN_BATTERY_ADC 35

/* Spannungsteiler der Akkumessung: zwei gleiche Widerstaende halbieren. */
#define BATTERY_DIVIDER 2.0f

#endif /* CONFIG_H */
