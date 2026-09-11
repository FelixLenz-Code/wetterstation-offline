"""Tests der konformalen Bandaufweitung.

Quantilregression liefert Bänder, die auf den Trainingsdaten passen und auf neuen
Daten regelmässig zu eng sind. Gemessen an einem vollen Durchlauf deckte ein
10-bis-90-Prozent-Band nur 51 statt 80 Prozent der Fälle ab.

Ein Band, das seine eigene Zusage um dreissig Punkte verfehlt, ist schlimmer als
gar keines -- es sieht nach Wissen aus. Deshalb hier die Zusicherung, dass die
Aufweitung die versprochene Abdeckung wirklich herstellt.
"""

from __future__ import annotations

import numpy as np
import pytest

from wetter.models.train import conformal_width


@pytest.fixture
def lage():
    """Ein zu enges Band: die Wahrheit streut stärker, als das Modell annimmt."""
    rng = np.random.default_rng(12)
    n = 2000
    mitte = rng.normal(10.0, 5.0, n)
    # Modell behauptet plus/minus 1 K, tatsächlich streut es mit 2 K.
    unten, oben = mitte - 1.0, mitte + 1.0
    beobachtet = mitte + rng.normal(0.0, 2.0, n)
    return unten, oben, beobachtet


def abdeckung(unten, oben, y, breite=0.0) -> float:
    return float(np.mean((y >= unten - breite) & (y <= oben + breite)))


def test_aufweitung_stellt_die_zusage_her(lage):
    """Die eigentliche Zusicherung."""
    unten, oben, y = lage
    vorher = abdeckung(unten, oben, y)
    assert vorher < 0.45  # das zu enge Ausgangsband

    breite = conformal_width(unten, oben, y, coverage=0.8)
    nachher = abdeckung(unten, oben, y, breite)
    assert nachher == pytest.approx(0.8, abs=0.03)


def test_aufweitung_auf_einem_zweiten_abschnitt_haelt(lage):
    """Auf Daten, die die Kalibrierung nicht gesehen hat.

    Die Garantie gilt nur dann etwas, wenn sie auch auf frischen Fällen hält --
    sonst hätte man das Band nur an die Kalibrierdaten angepasst.
    """
    unten, oben, y = lage
    haelfte = len(y) // 2
    breite = conformal_width(unten[:haelfte], oben[:haelfte], y[:haelfte], coverage=0.8)
    frisch = abdeckung(unten[haelfte:], oben[haelfte:], y[haelfte:], breite)
    assert frisch == pytest.approx(0.8, abs=0.05)


@pytest.mark.parametrize("ziel", [0.5, 0.8, 0.9, 0.95])
def test_verschiedene_zusagen_werden_getroffen(lage, ziel):
    unten, oben, y = lage
    breite = conformal_width(unten, oben, y, coverage=ziel)
    assert abdeckung(unten, oben, y, breite) == pytest.approx(ziel, abs=0.04)


def test_ausreichend_weites_band_wird_nicht_veraendert():
    """Ein Band, das schon zu weit ist, wird nicht künstlich eingeengt.

    Das wäre zwar erlaubt, aber die Abdeckung ist die Zusage, die zählt -- ein
    zu weites Band hält sie, ein zu enges nicht.
    """
    rng = np.random.default_rng(3)
    mitte = rng.normal(10.0, 5.0, 1000)
    y = mitte + rng.normal(0.0, 0.3, 1000)
    breite = conformal_width(mitte - 5.0, mitte + 5.0, y, coverage=0.8)
    assert breite == 0.0


def test_zu_wenige_faelle_ergeben_keine_aufweitung():
    """Aus fünfzig Fällen lässt sich keine Garantie ableiten."""
    rng = np.random.default_rng(1)
    mitte = rng.normal(10.0, 3.0, 50)
    y = mitte + rng.normal(0, 3.0, 50)
    assert conformal_width(mitte - 0.5, mitte + 0.5, y, coverage=0.8) == 0.0


def test_fehlwerte_werden_uebergangen(lage):
    unten, oben, y = lage
    y = y.copy()
    y[::7] = np.nan
    breite = conformal_width(unten, oben, y, coverage=0.8)
    gueltig = np.isfinite(y)
    assert abdeckung(unten[gueltig], oben[gueltig], y[gueltig], breite) == pytest.approx(
        0.8, abs=0.04
    )


def test_endliche_korrektur_ist_konservativ():
    """Die Korrektur um (n+1)/n macht das Band bei wenigen Fällen eher zu weit.

    Das ist der Kern der endlichen Garantie: lieber etwas zu vorsichtig als eine
    Zusage, die schon bei kleiner Stichprobe nicht hält.
    """
    rng = np.random.default_rng(5)
    treffer = []
    for _ in range(40):
        mitte = rng.normal(10.0, 4.0, 150)
        unten, oben = mitte - 1.0, mitte + 1.0
        y = mitte + rng.normal(0.0, 2.0, 150)
        breite = conformal_width(unten, oben, y, coverage=0.8)
        pruef_mitte = rng.normal(10.0, 4.0, 500)
        pruef_y = pruef_mitte + rng.normal(0.0, 2.0, 500)
        treffer.append(
            abdeckung(pruef_mitte - 1.0, pruef_mitte + 1.0, pruef_y, breite)
        )
    # Im Mittel mindestens die Zusage, nicht darunter.
    assert float(np.mean(treffer)) >= 0.78
