"""Tests der Vorhersageziele.

Der wichtigste Test dieser Datei ist der auf Durchgriff: ein Ziel darf ausschliesslich
Zukunft beschreiben. Liest es versehentlich die Gegenwart mit, sieht das Modell im
Training grossartig aus und ist im Betrieb wertlos -- und zwar ohne dass irgendein
anderer Test Alarm schlägt. Deshalb wird hier nicht das Ergebnis geprüft, sondern die
Zeitkonvention selbst.

Konvention: ein Ziel mit Vorlaufzeit ``h`` zum Zeitpunkt ``t`` beschreibt das Fenster
``(t, t+h]`` -- die Gegenwart gehört nicht dazu, das Ende schon.
"""

import numpy as np
import pandas as pd
import pytest

from wetter.features.targets import (
    RAIN_LEADS,
    TEMP_LEADS,
    build_targets,
    extreme_in_next,
    rain_in_next,
    value_at,
)


def reihe(werte) -> pd.Series:
    idx = pd.date_range("2026-01-01", periods=len(werte), freq="h", tz="UTC")
    return pd.Series(werte, index=idx, dtype=float)


# --- Kein Durchgriff auf die Gegenwart ---------------------------------------


def test_regen_jetzt_faellt_nicht_ins_ziel():
    """Der entscheidende Test.

    Es regnet nur in der Stunde 0 und danach nie wieder. Ein Ziel zum Zeitpunkt 0
    muss deshalb trocken sein -- wer die eigene Stunde mitzählt, bekommt hier eine
    1 und trainiert ein Modell, das den Regen vorhersagt, den es bereits sieht.
    """
    regen = reihe([5.0] + [0.0] * 12)
    ziel = rain_in_next(regen, 6)
    assert ziel.iloc[0] == 0.0


def test_regen_in_der_naechsten_stunde_zaehlt():
    regen = reihe([0.0, 5.0] + [0.0] * 11)
    assert rain_in_next(regen, 6).iloc[0] == 1.0


def test_regen_am_fensterende_zaehlt_noch():
    """Das Fenster ist rechts geschlossen: (t, t+h] schliesst t+h ein."""
    regen = reihe([0.0] * 6 + [5.0] + [0.0] * 6)
    assert rain_in_next(regen, 6).iloc[0] == 1.0


def test_regen_knapp_nach_dem_fenster_zaehlt_nicht():
    regen = reihe([0.0] * 7 + [5.0] + [0.0] * 5)
    assert rain_in_next(regen, 6).iloc[0] == 0.0


def test_wert_in_h_stunden_ist_wirklich_h_stunden_spaeter():
    werte = reihe(range(20))
    ziel = value_at(werte, 6)
    assert ziel.iloc[0] == 6.0
    assert ziel.iloc[5] == 11.0


def test_extremwert_ignoriert_die_gegenwart():
    """Der aktuelle Wert ist der kälteste -- trotzdem darf er nicht das Minimum sein."""
    temp = reihe([-10.0] + [5.0] * 12)
    assert extreme_in_next(temp, 6, how="min").iloc[0] == 5.0


def test_extremwert_findet_das_maximum_im_fenster():
    temp = reihe([0.0, 1.0, 2.0, 9.0, 3.0, 4.0, 5.0, 99.0, 0.0])
    assert extreme_in_next(temp, 6, how="max").iloc[0] == 9.0


# --- Unvollstaendige Fenster -------------------------------------------------


def test_unvollstaendiges_fenster_am_reihenende_ist_nan():
    """Sonst würde die letzte Stunde als trocken gelten, nur weil nichts mehr da ist."""
    regen = reihe([0.0] * 8)
    ziel = rain_in_next(regen, 6)
    assert ziel.iloc[-6:].isna().all()


def test_luecke_im_fenster_macht_das_ziel_unbekannt():
    """Eine Messlücke als kein Regen zu werten, wäre der Weg zu geschönten Zahlen."""
    regen = reihe([0.0, 0.0, np.nan, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert np.isnan(rain_in_next(regen, 6).iloc[0])


def test_vollstaendiges_fenster_ohne_luecke_ist_bekannt():
    regen = reihe([0.0] * 10)
    assert rain_in_next(regen, 6).iloc[0] == 0.0


# --- Zusammenspiel im Zielsatz ----------------------------------------------


@pytest.fixture
def rohdaten() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=200, freq="h", tz="UTC")
    rng = np.random.default_rng(3)
    return pd.DataFrame(
        {
            "temperature_c": 10.0 + 5.0 * np.sin(np.arange(200) / 4.0),
            "precip_mm": (rng.random(200) < 0.2) * rng.gamma(1.5, 0.7, 200),
            "wind_speed_ms": rng.gamma(2.0, 1.5, 200),
        },
        index=idx,
    )


def test_alle_vorlaufzeiten_werden_gebaut(rohdaten):
    ziele = build_targets(rohdaten)
    for h in RAIN_LEADS:
        assert f"rain_next_{h}h" in ziele
    for h in TEMP_LEADS:
        assert f"temp_at_{h}h" in ziele


def test_laengere_fenster_sind_nie_trockener(rohdaten):
    """Logische Zusicherung: was in 6 Stunden nass ist, ist es in 24 auch."""
    ziele = build_targets(rohdaten)
    kurz = ziele["rain_next_6h"]
    lang = ziele["rain_next_24h"]
    beide = kurz.notna() & lang.notna()
    assert (lang[beide] >= kurz[beide]).all()


def test_ziel_ohne_quelle_entfaellt():
    """Ohne Niederschlagsmessung gibt es kein Regenziel statt einer Spalte voll NaN."""
    idx = pd.date_range("2026-01-01", periods=100, freq="h", tz="UTC")
    nur_temp = pd.DataFrame({"temperature_c": np.zeros(100)}, index=idx)
    ziele = build_targets(nur_temp)
    assert not any(c.startswith("rain_") for c in ziele.columns)
    assert "temp_at_24h" in ziele.columns


def test_tagesminimum_und_maximum_umschliessen_den_verlauf(rohdaten):
    ziele = build_targets(rohdaten)
    beide = ziele["temp_min_24h"].notna() & ziele["temp_max_24h"].notna()
    assert (ziele.loc[beide, "temp_min_24h"] <= ziele.loc[beide, "temp_max_24h"]).all()


def test_ziele_verschieben_sich_gegeneinander_richtig(rohdaten):
    """temp_at_12h zum Zeitpunkt t muss temp_at_6h zum Zeitpunkt t+6h entsprechen."""
    ziele = build_targets(rohdaten)
    a = ziele["temp_at_12h"].iloc[10]
    b = ziele["temp_at_6h"].iloc[16]
    assert a == pytest.approx(b)
