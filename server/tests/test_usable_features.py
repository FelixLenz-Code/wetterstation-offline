"""Tests der Merkmalsauswahl nach Verfügbarkeit.

Entstanden aus einer Messung: ein Sechs-Stunden-Temperaturmodell verliert an
Abdeckung von 80 auf 66 Prozent und an Genauigkeit von 1,58 auf 1,76 K, wenn man
ihm die Strahlungs- und Sichtmerkmale wegnimmt. Genau das passiert im Betrieb,
wenn auf DWD-Merkmalen trainiert wird, die die eigene Station nie liefern kann --
das Modell hat Trennungen gelernt, die es nie wieder anwenden kann.
"""

from __future__ import annotations

from wetter.features.build import feature_names, usable_features
from wetter.worker.forecast import HOURLY_COLUMNS


def test_sonnenschein_und_sicht_sind_nicht_lieferbar():
    """Für beide gibt es an der Station keinen Sensor und keine Spalte."""
    lieferbar = set(usable_features(list(HOURLY_COLUMNS)))
    assert "sunshine_min" not in lieferbar
    assert "visibility" not in lieferbar


def test_fast_alle_merkmale_bleiben_uebrig():
    """Die Einschränkung soll zwei Merkmale kosten, nicht das halbe Modell."""
    alle = set(feature_names())
    lieferbar = set(usable_features(list(HOURLY_COLUMNS)))
    assert lieferbar <= alle
    assert len(alle - lieferbar) == 2


def test_kernmerkmale_sind_immer_dabei():
    lieferbar = set(usable_features(list(HOURLY_COLUMNS)))
    for merkmal in (
        "pressure",
        "pressure_delta_3h",
        "temperature",
        "dewpoint",
        "dewpoint_spread",
        "wind_u",
        "wind_v",
        "precip_sum_24h",
        "hours_since_rain",
        "hour_sin",
        "doy_cos",
    ):
        assert merkmal in lieferbar, merkmal


def test_weniger_spalten_ergeben_weniger_merkmale():
    """Eine Station ohne Regenmesser kann keine Niederschlagssummen liefern."""
    ohne_regen = [c for c in HOURLY_COLUMNS if c != "precip_mm"]
    lieferbar = set(usable_features(ohne_regen))
    assert "precip_sum_24h" not in lieferbar
    assert "hours_since_rain" not in lieferbar
    # Die Drucktendenz bleibt davon unberührt.
    assert "pressure_delta_3h" in lieferbar


def test_zeitmerkmale_brauchen_gar_keine_messung():
    """Uhrzeit und Jahreszeit stehen auch einer Station ohne jeden Sensor zur
    Verfügung -- sie stammen aus dem Zeitstempel."""
    lieferbar = set(usable_features([]))
    assert {"hour_sin", "hour_cos", "doy_sin", "doy_cos"} <= lieferbar


def test_ohne_temperatur_kein_taupunkt():
    ohne = [c for c in HOURLY_COLUMNS if c != "temperature_c"]
    lieferbar = set(usable_features(ohne))
    assert "dewpoint_spread" not in lieferbar
    assert "temperature_delta_3h" not in lieferbar
