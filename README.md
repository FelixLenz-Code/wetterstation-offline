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
| DWD-Import (11 Messgrößen, Stationssuche, Bootstrap) | fertig, gegen echte Daten geprüft |
| Meteorologische Umrechnungen | fertig, gegen DWD-Rechnung validiert |
| Merkmalsbau (45 Merkmale) + Klimatologie | fertig |
| Baselines (Persistenz, Klimatologie, Zambretti) | fertig |
| Verifikation (Brier, BSS, CRPS, Zuverlässigkeit) | fertig |
| Training (LightGBM: Regen + Temperatur) | fertig, Güte gemessen |
| Datenbank (9 Tabellen, Alembic) | fertig, gegen Postgres 17 geprüft |
| Sensorzustände und Testmodus | fertig |
| MQTT-Ingest mit Plausibilitätsprüfung | fertig, Ende zu Ende geprüft |
| Firmware: Ringpuffer, Windfahne | fertig, 22 Host-Tests |
| Firmware: Sensortreiber, MQTT, Energieverwaltung | offen |
| Stundenaggregation, Inferenz-Takt, Re-Training | offen |
| PWA und Deployment | offen |

**104 Tests** im Server (11 davon gegen echtes Postgres), **22 Host-Tests** in der
Firmware, alles in der CI.

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
| 6 h | 3,42 | 3,81 | **1,54** | +55,0 % |
| 12 h | 3,42 | 5,28 | **2,01** | +41,4 % |
| 24 h | 3,42 | 2,75 | **2,31** | +32,6 % |
| 48 h | 3,42 | 3,49 | **2,89** | +15,6 % |

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
```

## Datenquelle

Historische Messreihen: **Deutscher Wetterdienst**, Climate Data Center
(`opendata.dwd.de`), frei nutzbar nach GeoNutzV. Die Quellenangabe ist Pflicht und
gehört in den Impressum-Bereich der PWA.
