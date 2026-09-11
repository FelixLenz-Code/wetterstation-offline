# Wetterstation

Eine selbstgebaute Wetterstation mit **eigener Vorhersage ohne Internet**. Eine
ESP32-Außenstation misst, ein Server im Haus nimmt die Daten auf, trainiert
Vorhersagemodelle und zeigt das Ergebnis in einer PWA.

Der Kern ist nicht das Messen — das können Fertigstationen auch — sondern die Frage,
wie weit man mit einer einzelnen Bodenstation und ehrlich bewerteten Modellen kommt.

## Der Trick mit dem Bootstrap

Ein Modell nur auf eigenen Sensordaten bräuchte ein bis zwei Jahre, bevor es eine
simple Faustformel schlägt. Deshalb wird mit **historischen DWD-Messreihen** der
nächstgelegenen Stationen trainiert — für Freiburg sind das 31 Jahre lückenloser
Stundenwerte. Der laufende Betrieb und jede Vorhersage laufen danach vollständig
offline.

Damit das trägt, müssen eigene Sensoren und DWD-Daten exakt dasselbe Merkmalsschema
liefern. Der ganze Aufwand in `server/wetter/features/meteo.py` — Druckreduktion auf
Meeresniveau, Taupunkt, Windkomponenten — dient genau diesem Zweck.

## Stand

| Teil | Status |
| --- | --- |
| DWD-Import (11 Messgrößen, Stationssuche, Bootstrap) | fertig, 272.000 Stunden in 17 s |
| Meteorologische Umrechnungen | fertig, gegen DWD-Rechnung validiert |
| Merkmalsbau (45 Merkmale) + Klimatologie | fertig |
| Baselines (Persistenz, Klimatologie, Zambretti) | fertig |
| Verifikation (Brier, BSS, CRPS, Zuverlässigkeit) | fertig |
| Training, Modell-Register, Schattenbetrieb | fertig, Güte gemessen |
| Bias-Korrektur und konformale Bandeichung | fertig |
| Datenbank (10 Tabellen, Alembic) | fertig, gegen Postgres 17 geprüft |
| Sensorzustände und Testmodus | fertig |
| MQTT-Ingest, Befehlswarteschlange | fertig, Ende zu Ende geprüft |
| PWA: Jetzt, Verlauf, Vorhersage, Güte, Sensoren | fertig |
| Deployment: Compose, Mosquitto, install.sh | fertig |
| Firmware: Ringpuffer, Windfahne, Sensorverwaltung | fertig, host-getestet |
| Firmware: BME280, Impulszählung, Energieverwaltung | fertig, host-getestet |
| Firmware: WLAN, MQTT, Zeitabgleich, Hauptprogramm | fertig, übersetzt |
| Firmware: MLX90614, AS3935, BH1750, INA219 | offen |
| Web-Push-Warnungen, Service Worker | offen |

**263 Server-Tests** (davon rund 70 gegen echtes Postgres) und **71 Host-Tests** in
der Firmware. Die CI übersetzt zusätzlich die vollständige Firmware gegen ESP-IDF.

## Gemessene Güte

Trainiert auf 1995–2017, kalibriert auf 2017–2022, getestet auf **2022–2026 —
Jahren, die kein Modell und keine Kalibrierung je gesehen hat**. Station Freiburg.

### Regen (Brier Score, kleiner ist besser)

| Vorlauf | Basisrate | Klimatologie | Persistenz | Zambretti | **Modell** | Gewinn vs. Zambretti |
| --- | --- | --- | --- | --- | --- | --- |
| 6 h | 22,7 % | 0,1770 | 0,1373 | 0,1564 | **0,1000** | +36,0 % |
| 12 h | 32,0 % | 0,2199 | 0,1842 | 0,1866 | **0,1250** | +33,1 % |
| 18 h | 38,8 % | 0,2404 | 0,2095 | 0,2007 | **0,1402** | +30,4 % |
| 24 h | 44,4 % | 0,2502 | 0,2232 | 0,2070 | **0,1507** | +27,6 % |

Der Kalibrierungsfehler liegt bei 0,8 bis 1,3 Prozentpunkten — „70 % Regen" heißt
also tatsächlich ungefähr 70 %.

### Temperatur (mittlerer absoluter Fehler in Kelvin)

| Vorlauf | Klimatologie | Persistenz | **Modell** | Gewinn |
| --- | --- | --- | --- | --- |
| 6 h | 3,42 | 3,81 | **1,60** | +53 % |
| 24 h | 3,42 | 2,75 | **2,31** | +32 % |
| 48 h | 3,42 | 3,49 | **2,89** | +16 % |

Das 10-bis-90-Prozent-Band trifft über 2022 bis 2026 in **79 %** der Fälle -- also
genau so oft, wie es verspricht. Die Abdeckung schwankt allerdings deutlich über das
Jahr, von 85 % im März bis 68 % im Juni: der Sommer ist schwerer vorherzusagen.
Deshalb wird das Band je Kalendermonat eigens geeicht.

Der Abfall zu 48 Stunden hin ist keine Schwäche der Umsetzung, sondern die
physikalische Grenze: eine Punktmessung sieht die großräumige Wetterlage nicht.

## Aufbau

```
firmware/station/   ESP-IDF-Projekt (C11, CMake) für den ESP32-WROOM-32
firmware/tests/     Host-Tests der reinen Rechenlogik, ohne Hardware lauffähig
hardware/           Verdrahtung, Kalibrierung, Stückliste
server/             Python: DWD-Import, Merkmale, Modelle, Ingest, API
web/                Next.js-PWA
docs/
```

## Entwickeln

```bash
# Server
cd server
python3 -m venv .venv && ./.venv/bin/pip install -e '.[dev]'
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/ruff check wetter/ tests/

# Firmware-Logik (braucht kein ESP-IDF)
cd firmware/tests && make check

# Firmware übersetzen (braucht ESP-IDF v5.5)
. ~/esp/esp-idf/export.sh
cd firmware/station
cp main/config.example.h main/config.h   # und ausfüllen
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor

# Weboberfläche
cd web && npm run dev
```

Die Zugangsdaten für `config.h` liefert `./install.sh station` auf dem Server, samt
der richtigen IP.

## Datenquelle

Historische Messreihen: **Deutscher Wetterdienst**, Climate Data Center
(`opendata.dwd.de`), frei nutzbar nach GeoNutzV. Die Quellenangabe ist Pflicht und
gehört in den Impressum-Bereich der PWA.
