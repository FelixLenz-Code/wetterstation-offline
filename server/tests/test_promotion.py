"""Tests der Ablöse-Entscheidung.

Der Fehler, gegen den diese Regeln schützen, ist teuer und unsichtbar: ein
schlechteres Modell liefert weiterhin plausibel aussehende Zahlen, und dass sie
schlechter geworden sind, merkt man erst nach Wochen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from wetter.db.models import Forecast, Model, Station, Verification
from wetter.models.registry import STATUS_ACTIVE, STATUS_SHADOW, promote
from wetter.worker.promotion import compare, evaluate_shadow

JETZT = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def station(session) -> Station:
    s = Station(
        key="p",
        name="P",
        latitude=48.0,
        longitude=8.0,
        altitude_m=200.0,
        created_at=JETZT,
    )
    session.add(s)
    session.flush()
    return s


def _modell(session, status: str, target: str = "rain", lead: int = 6) -> Model:
    m = Model(
        kind="lightgbm_binary",
        target=target,
        lead_hours=lead,
        status=status,
        feature_names=["pressure"],
        metrics={},
        trained_at=JETZT,
        train_start=JETZT - timedelta(days=365),
        train_end=JETZT,
        source="dwd",
    )
    session.add(m)
    session.flush()
    return m


def _vorhersagen(
    session, station: Station, modell: Model, scores: list[float], *, offset: int = 0
) -> None:
    """Legt bewertete Vorhersagen mit vorgegebenem Gütemass an."""
    schluessel = "brier" if modell.target == "rain" else "absolute_error"
    for i, score in enumerate(scores):
        ausgestellt = JETZT - timedelta(hours=len(scores) - i + offset)
        f = Forecast(
            model_id=modell.id,
            station_id=station.id,
            issued_at=ausgestellt,
            valid_at=ausgestellt + timedelta(hours=modell.lead_hours),
            target=modell.target,
            value=0.5,
        )
        session.add(f)
        session.flush()
        session.add(
            Verification(
                forecast_id=f.id,
                verified_at=JETZT,
                observed=1.0,
                scores={schluessel: score},
            )
        )
    session.flush()


def test_ohne_aktives_modell_uebernimmt_der_schatten_sofort(session, station):
    """Irgendeine Vorhersage ist besser als gar keine."""
    schatten = _modell(session, STATUS_SHADOW)
    ergebnis = compare(session, schatten.id, now=JETZT)
    assert ergebnis.active_id is None
    assert "kein aktives Modell" in ergebnis.reason

    evaluate_shadow(session, now=JETZT)
    session.flush()
    assert session.get(Model, schatten.id).status == STATUS_ACTIVE


def test_zu_wenige_faelle_reichen_nicht(session, station):
    """Über zwanzig Vorhersagen lässt sich nichts entscheiden."""
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.20] * 20)
    _vorhersagen(session, station, schatten, [0.05] * 20)

    ergebnis = compare(session, schatten.id, now=JETZT)
    assert not ergebnis.promoted
    assert "gemeinsam bewertete" in ergebnis.reason
    assert session.get(Model, schatten.id).status == STATUS_SHADOW


def test_deutlich_besseres_modell_wird_befoerdert(session, station):
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.20] * 300)
    _vorhersagen(session, station, schatten, [0.12] * 300)

    ergebnis = compare(session, schatten.id, now=JETZT)
    assert ergebnis.common == 300
    assert ergebnis.improvement == pytest.approx(0.4, abs=0.01)
    assert ergebnis.promoted


def test_knapp_besseres_modell_bleibt_im_schatten(session, station):
    """Ein Prozent Unterschied ist Rauschen, kein Fortschritt."""
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.200] * 300)
    _vorhersagen(session, station, schatten, [0.198] * 300)

    ergebnis = compare(session, schatten.id, now=JETZT)
    assert not ergebnis.promoted
    assert "unter der Schwelle" in ergebnis.reason


def test_schlechteres_modell_wird_nicht_befoerdert(session, station):
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.10] * 300)
    _vorhersagen(session, station, schatten, [0.25] * 300)

    ergebnis = compare(session, schatten.id, now=JETZT)
    assert ergebnis.improvement < 0
    assert not ergebnis.promoted
    assert session.get(Model, schatten.id).status == STATUS_SHADOW


def test_nur_gemeinsame_zeitpunkte_werden_verglichen(session, station):
    """Sonst gewinnt schlicht das Modell, das die ruhigere Woche erwischt hat.

    Das Schattenmodell bekommt zusätzlich 300 sehr gute Werte aus einem Zeitraum,
    in dem das aktive Modell gar nicht lief. Zählte man die mit, sähe es besser aus,
    als es ist.
    """
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.20] * 300)
    _vorhersagen(session, station, schatten, [0.20] * 300)
    _vorhersagen(session, station, schatten, [0.001] * 300, offset=500)

    ergebnis = compare(session, schatten.id, now=JETZT)
    assert ergebnis.common == 300
    assert ergebnis.shadow_score == pytest.approx(0.20)
    assert not ergebnis.promoted


def test_alte_vorhersagen_fallen_aus_dem_fenster(session, station):
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    # 100 Tage alt, das Fenster umfasst 30.
    _vorhersagen(session, station, aktiv, [0.20] * 300, offset=2400)
    _vorhersagen(session, station, schatten, [0.05] * 300, offset=2400)

    ergebnis = compare(session, schatten.id, now=JETZT, window_days=30)
    assert ergebnis.common == 0
    assert not ergebnis.promoted


def test_befoerderung_versetzt_das_alte_in_den_ruhestand(session, station):
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.20] * 300)
    _vorhersagen(session, station, schatten, [0.10] * 300)

    evaluate_shadow(session, now=JETZT)
    session.flush()
    assert session.get(Model, schatten.id).status == STATUS_ACTIVE
    assert session.get(Model, aktiv.id).status == "retired"


def test_temperaturmodell_nutzt_den_absoluten_fehler(session, station):
    aktiv = _modell(session, STATUS_ACTIVE, target="temperature", lead=24)
    schatten = _modell(session, STATUS_SHADOW, target="temperature", lead=24)
    _vorhersagen(session, station, aktiv, [2.5] * 300)
    _vorhersagen(session, station, schatten, [2.0] * 300)

    ergebnis = compare(session, schatten.id, now=JETZT)
    assert ergebnis.active_score == pytest.approx(2.5)
    assert ergebnis.improvement == pytest.approx(0.2, abs=0.01)
    assert ergebnis.promoted


def test_verschiedene_vorlaufzeiten_stoeren_sich_nicht(session, station):
    """Ein Modell für 6 Stunden darf keines für 24 Stunden ablösen."""
    aktiv6 = _modell(session, STATUS_ACTIVE, lead=6)
    aktiv24 = _modell(session, STATUS_ACTIVE, lead=24)
    schatten6 = _modell(session, STATUS_SHADOW, lead=6)
    _vorhersagen(session, station, aktiv6, [0.20] * 300)
    _vorhersagen(session, station, schatten6, [0.10] * 300)

    promote(session, schatten6.id)
    session.flush()
    assert session.get(Model, aktiv6.id).status == "retired"
    # Das 24-Stunden-Modell bleibt unangetastet.
    assert session.get(Model, aktiv24.id).status == STATUS_ACTIVE


def test_kein_schattenmodell_ergibt_keine_aenderung(session, station):
    aktiv = _modell(session, STATUS_ACTIVE)
    assert evaluate_shadow(session, now=JETZT) == []
    assert session.get(Model, aktiv.id).status == STATUS_ACTIVE


def test_unbekanntes_modell_wirft(session):
    with pytest.raises(ValueError, match="nicht gefunden"):
        compare(session, 999999, now=JETZT)


def test_alle_modelle_bleiben_auffindbar(session, station):
    """Auch ein abgelöstes Modell verschwindet nicht -- die Historie zählt."""
    aktiv = _modell(session, STATUS_ACTIVE)
    schatten = _modell(session, STATUS_SHADOW)
    _vorhersagen(session, station, aktiv, [0.20] * 300)
    _vorhersagen(session, station, schatten, [0.10] * 300)
    evaluate_shadow(session, now=JETZT)
    session.flush()
    assert len(session.scalars(select(Model)).all()) == 2
