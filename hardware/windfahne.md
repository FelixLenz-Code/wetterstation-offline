# Windfahne kalibrieren

Die Windfahne des WH-SP-WS01-Sets ist kein Drehgeber, sondern ein Ring aus acht
Reed-Kontakten mit je einem Widerstand. Steht die Fahne genau auf einem Kontakt,
schließt einer. Steht sie dazwischen, schließen zwei benachbarte gleichzeitig, und die
Parallelschaltung ergibt einen weiteren Wert. So entstehen 16 Widerstände für 16
Richtungen im Abstand von 22,5°.

## Warum das kalibriert werden muss

Die Nennwerte liegen an einer Stelle sehr eng beieinander:

| Richtung | Nennwiderstand |
| --- | --- |
| 67,5° (ONO) | 891 Ω |
| 90° (O) | 1000 Ω |

Das sind nur **12 % Abstand**. Ein Messwert kippt bei 942 Ω von der einen zur anderen
Richtung — also schon **5,76 % neben dem Sollwert**. Dasselbe gilt für 14,12 kΩ und
16 kΩ (6,24 % Reserve).

Die in solchen Fahnen üblichen 5-%-Widerstände reichen dafür nicht sicher aus. Und der
Fehler meldet sich nicht: die Station zeigt dann still eine um 22,5° verdrehte
Windrichtung. Weil die Windrichtungsänderung eines der stärksten Merkmale für die
Regenvorhersage ist, verdirbt das nicht nur die Anzeige, sondern auch das Modell.

Die Firmware setzt deshalb `WIND_VANE_MAX_TOLERANCE` auf 5,5 % und lehnt eine
Kalibriertabelle ab, die diese Grenze verletzt.

## Vorgehen

1. Fahne abziehen und den Stecker freilegen. Es genügt ein Multimeter im
   Widerstandsbereich.
2. Fahne von Hand in 22,5°-Schritten drehen und jeweils den Widerstand notieren.
   Beginnen bei Nord (0°), dann im Uhrzeigersinn.
3. Die 16 Werte in der Reihenfolge 0°, 22,5°, 45° … 337,5° eintragen und per
   `wind_vane_set_table()` übernehmen. Die Funktion prüft selbst, ob die Tabelle
   eindeutig bleibt, und lehnt sie sonst ab.
4. Zur Kontrolle `wind_vane_min_separation()` aufrufen — der Wert muss deutlich über
   der eingestellten Toleranz liegen.

## Beschaltung

Spannungsteiler gegen Masse:

```
    3,3 V ──[ Fahne ]──┬── ADC
                       │
                    [ 10 kΩ ]
                       │
                      GND
```

Der Serienwiderstand von 10 kΩ ist ein Kompromiss: er liegt geometrisch etwa in der
Mitte des Tabellenbereichs (688 Ω bis 120 kΩ) und hält die Teilerspannung damit über
den ganzen Bereich in einem gut auflösbaren Fenster.

> Der ADC des klassischen ESP32 ist nichtlinear und braucht die eingebaute
> Kalibrierung (`esp_adc_cal`). Ohne sie kommen je nach Chip mehrere Prozent Fehler
> dazu — bei 5,76 % Reserve ist das genau der Unterschied zwischen richtig und still
> falsch.
