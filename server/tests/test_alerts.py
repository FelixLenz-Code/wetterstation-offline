"""Tests der Warnlogik.

Der Kern ist probability_below: aus drei Quantilen die Wahrscheinlichkeit
schätzen, dass ein Wert darunter liegt. Frost liegt fast immer unterhalb des
untersten Quantils -- wer dort hart kappt, lässt jede Frostwarnung an derselben
Zahl kleben, egal wie kalt es wird.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from wetter.worker.alerts import (
    Alert,
    Rule,
    evaluate_frost,
    in_cooldown,
    probability_below,
)

JETZT = datetime(2026, 3, 15, 21, 0, tzinfo=UTC)


def quantile(unten: float, mitte: float, oben: float) -> dict[str, float]:
    return {"0.1": unten, "0.5": mitte, "0.9": oben}


# --- Wahrscheinlichkeit aus Quantilen ---------------------------------------


def test_median_liegt_bei_fuenfzig_prozent():
    assert probability_below(quantile(2.0, 5.0, 8.0), 5.0) == pytest.approx(0.5)


def test_quantile_treffen_ihre_eigenen_werte():
    q = quantile(2.0, 5.0, 8.0)
    assert probability_below(q, 2.0) == pytest.approx(0.1)
    assert probability_below(q, 8.0) == pytest.approx(0.9)


def test_zwischenwerte_werden_interpoliert():
    q = quantile(2.0, 5.0, 8.0)
    p = probability_below(q, 3.5)
    assert 0.1 < p < 0.5


def test_unterhalb_des_bandes_wird_fortgeschrieben():
    """Der wichtigere Fall: Frost liegt fast immer unter dem untersten Quantil.

    Würde dort gekappt, bekäme jede Frostnacht dieselben zehn Prozent -- egal ob
    das Band knapp über null liegt oder weit darüber.
    """
    q = quantile(2.0, 5.0, 8.0)
    knapp = probability_below(q, 1.0)
    deutlich = probability_below(q, -2.0)
    assert 0.0 < deutlich < knapp < 0.1


def test_ausläufer_faellt_nicht_zu_schnell_ab():
    """Der Grund für die Normalverteilung am Rand.

    Bei einem Band von 2 bis 8 Grad ist die Mitte nur fünf Grad über null --
    da darf die Wahrscheinlichkeit für Frost nicht schon null sein. Linear aus
    der Steigung im Inneren fortgeschrieben wäre sie das (die Steigung dort
    beschreibt den dichten Teil der Verteilung, nicht den Ausläufer), und eine
    Frostwarnung käme erst, wenn das ganze Band unter null liegt.
    """
    q = quantile(2.0, 5.0, 8.0)
    assert probability_below(q, 0.0) > 0.005
    # Aber auch nicht so träge, dass jede milde Nacht eine Warnung auslöst.
    assert probability_below(q, 0.0) < 0.10


def test_anschluss_an_die_stuetzstelle_ist_stetig():
    """Innen interpoliert, aussen Normalverteilung -- ohne Sprung dazwischen."""
    q = quantile(2.0, 5.0, 8.0)
    knapp_drunter = probability_below(q, 2.0 - 1e-6)
    genau = probability_below(q, 2.0)
    assert abs(knapp_drunter - genau) < 1e-4


def test_weit_unterhalb_wird_nicht_negativ():
    q = quantile(2.0, 5.0, 8.0)
    assert probability_below(q, -50.0) == pytest.approx(0.0, abs=1e-9)


def test_oberhalb_des_bandes_wird_fortgeschrieben():
    q = quantile(2.0, 5.0, 8.0)
    assert probability_below(q, 9.0) > 0.9
    assert probability_below(q, 50.0) == 1.0


def test_ergebnis_steigt_monoton():
    q = quantile(-1.0, 4.0, 9.0)
    werte = [probability_below(q, t) for t in np.linspace(-10, 20, 60)]
    assert all(b >= a - 1e-12 for a, b in itertools.pairwise(werte))


def test_engeres_band_ergibt_schaerfere_aussage():
    """Ein sicheres Modell soll bei derselben Mitte entschiedener antworten."""
    eng = probability_below(quantile(4.5, 5.0, 5.5), 4.0)
    weit = probability_below(quantile(1.0, 5.0, 9.0), 4.0)
    assert eng < weit


def test_kaputte_eingaben_ergeben_nan():
    assert np.isnan(probability_below({}, 0.0))
    assert np.isnan(probability_below({"0.5": 5.0}, 0.0))
    # Nicht steigende Quantile -- darf nicht durchrutschen.
    assert np.isnan(probability_below({"0.1": 8.0, "0.5": 5.0, "0.9": 2.0}, 6.0))


# --- Frostwarnung ------------------------------------------------------------


def regel(schwelle=0.0, mindest=0.3, sperre=360, zuletzt=None) -> Rule:
    return Rule(
        kind="FROST",
        enabled=True,
        threshold=schwelle,
        min_probability=mindest,
        cooldown_minutes=sperre,
        last_fired_at=zuletzt,
    )


def test_klare_frostnacht_loest_aus():
    vorhersagen = [(JETZT + timedelta(hours=8), quantile(-3.0, -1.0, 1.0))]
    warnung = evaluate_frost(regel(), vorhersagen)
    assert warnung is not None
    assert warnung.probability > 0.5
    assert "Frost" in warnung.title


def test_milde_nacht_loest_nicht_aus():
    vorhersagen = [(JETZT + timedelta(hours=8), quantile(6.0, 9.0, 12.0))]
    assert evaluate_frost(regel(), vorhersagen) is None


def test_grenzfall_haengt_an_der_wahrscheinlichkeit():
    """Bei Frost lohnt sich die Warnung schon ab dreissig Prozent.

    Einmal zu viel abdecken kostet zehn Minuten, einmal zu wenig die Ernte.
    """
    vorhersagen = [(JETZT + timedelta(hours=6), quantile(-1.5, 1.0, 3.5))]
    assert evaluate_frost(regel(mindest=0.3), vorhersagen) is not None
    assert evaluate_frost(regel(mindest=0.8), vorhersagen) is None


def test_warnt_vor_der_fruehesten_stunde_nicht_der_kaeltesten():
    """Wer um 22 Uhr erfährt, dass es um 5 Uhr friert, deckt ab.

    Wer erfährt, dass es um 7 Uhr am kältesten wird, hat die entscheidende
    Stunde verpasst.
    """
    frueh = JETZT + timedelta(hours=8)
    spaet = JETZT + timedelta(hours=10)
    vorhersagen = [
        (spaet, quantile(-5.0, -3.0, -1.0)),  # kälter
        (frueh, quantile(-2.0, -0.5, 1.0)),   # früher
    ]
    warnung = evaluate_frost(regel(), vorhersagen)
    assert warnung.valid_at == frueh


def test_warnung_nennt_wahrscheinlichkeit_und_uhrzeit():
    vorhersagen = [(JETZT + timedelta(hours=8), quantile(-3.0, -1.0, 1.0))]
    text = evaluate_frost(regel(), vorhersagen).text
    assert "%" in text
    assert "Uhr" in text
    assert "0 °C" in text


# --- Sperrfrist --------------------------------------------------------------


def test_frische_regel_ist_nicht_gesperrt():
    assert not in_cooldown(regel(), JETZT)


def test_kuerzlich_gemeldet_bleibt_gesperrt():
    """Sonst meldet sich dieselbe Frostnacht stündlich neu.

    Die Vorhersage ändert sich ja kaum -- nur die Uhrzeit.
    """
    assert in_cooldown(regel(zuletzt=JETZT - timedelta(hours=2)), JETZT)


def test_nach_der_sperrfrist_wieder_frei():
    assert not in_cooldown(regel(sperre=360, zuletzt=JETZT - timedelta(hours=7)), JETZT)


def test_zeitzonenlose_angabe_wird_als_utc_gelesen():
    naiv = (JETZT - timedelta(hours=2)).replace(tzinfo=None)
    assert in_cooldown(regel(zuletzt=naiv), JETZT)


def test_nachricht_ist_gueltiges_json():
    import json

    a = Alert(kind="FROST", title="Frostwarnung", text="Test", probability=0.7,
              valid_at=JETZT)
    d = json.loads(a.payload())
    assert d["titel"] == "Frostwarnung"
    assert d["art"] == "frost"
    assert d["url"].startswith("/")
