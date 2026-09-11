"""Entscheidet, ob ein Schattenmodell das aktive ablösen darf.

Die Regel ist bewusst streng, weil der Fehler teuer und unsichtbar ist: ein
schlechteres Modell liefert weiterhin plausibel aussehende Zahlen, und dass sie
schlechter geworden sind, merkt man erst nach Wochen.

Drei Bedingungen müssen zusammenkommen:

1. **Genug Fälle.** Über zwanzig Vorhersagen lässt sich nichts entscheiden; bei
   seltenen Ereignissen wie Regen schon gar nicht.
2. **Derselbe Zeitraum.** Verglichen wird nur, was beide Modelle vorhergesagt haben.
   Sonst gewinnt schlicht das Modell, das die ruhigere Woche erwischt hat.
3. **Ein echter Vorsprung.** Ein Prozent Unterschied ist Rauschen. Gefordert wird ein
   Mindestabstand, und bei Gleichstand bleibt das alte Modell -- es hat sich bewährt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from wetter.db.models import Forecast, Model, Verification
from wetter.models.registry import STATUS_ACTIVE, STATUS_SHADOW, promote

log = logging.getLogger(__name__)

#: Mindestzahl gemeinsam bewerteter Vorhersagen.
MIN_COMMON = 200

#: Um soviel muss der Fehler kleiner sein, damit abgelöst wird (relativ).
MIN_IMPROVEMENT = 0.03

#: Fenster, über das verglichen wird.
WINDOW_DAYS = 30

#: Welches Gütemass zu welchem Ziel gehört.
SCORE_KEY = {"rain": "brier", "temperature": "absolute_error"}


@dataclass
class Comparison:
    """Ergebnis eines Vergleichs zwischen Schatten- und aktivem Modell."""

    shadow_id: int
    active_id: int | None
    target: str
    lead_hours: int
    common: int
    shadow_score: float
    active_score: float
    improvement: float
    promoted: bool
    reason: str


def _scores(
    session: Session, model_id: int, since: datetime
) -> dict[datetime, float]:
    """Gütemass je Ausstellungszeitpunkt eines Modells."""
    row = session.get(Model, model_id)
    schluessel = SCORE_KEY.get(row.target if row else "", "")
    if not schluessel:
        return {}

    zeilen = session.execute(
        select(Forecast.issued_at, Verification.scores)
        .join(Verification, Verification.forecast_id == Forecast.id)
        .where(Forecast.model_id == model_id, Forecast.issued_at >= since)
    ).all()
    return {
        zeit: float(werte[schluessel])
        for zeit, werte in zeilen
        if werte and schluessel in werte
    }


def compare(
    session: Session,
    shadow_id: int,
    *,
    now: datetime | None = None,
    window_days: int = WINDOW_DAYS,
    min_common: int = MIN_COMMON,
    min_improvement: float = MIN_IMPROVEMENT,
) -> Comparison:
    """Vergleicht ein Schattenmodell mit dem aktiven für dasselbe Ziel."""
    jetzt = now or datetime.now(UTC)
    seit = jetzt - timedelta(days=window_days)

    schatten = session.get(Model, shadow_id)
    if schatten is None:
        raise ValueError(f"Modell {shadow_id} nicht gefunden")

    aktiv = session.scalar(
        select(Model).where(
            Model.target == schatten.target,
            Model.lead_hours == schatten.lead_hours,
            Model.status == STATUS_ACTIVE,
        )
    )

    leer = Comparison(
        shadow_id=shadow_id,
        active_id=aktiv.id if aktiv else None,
        target=schatten.target,
        lead_hours=schatten.lead_hours,
        common=0,
        shadow_score=float("nan"),
        active_score=float("nan"),
        improvement=float("nan"),
        promoted=False,
        reason="",
    )

    if aktiv is None:
        # Gibt es noch kein aktives Modell, übernimmt das Schattenmodell sofort --
        # irgendetwas ist besser als gar keine Vorhersage.
        leer.reason = "kein aktives Modell vorhanden"
        return leer

    s_werte = _scores(session, shadow_id, seit)
    a_werte = _scores(session, aktiv.id, seit)
    gemeinsam = sorted(set(s_werte) & set(a_werte))
    leer.common = len(gemeinsam)

    if len(gemeinsam) < min_common:
        leer.reason = (
            f"nur {len(gemeinsam)} gemeinsam bewertete Vorhersagen, "
            f"mindestens {min_common} nötig"
        )
        return leer

    s = float(np.mean([s_werte[z] for z in gemeinsam]))
    a = float(np.mean([a_werte[z] for z in gemeinsam]))
    leer.shadow_score = s
    leer.active_score = a
    # Kleinerer Fehler ist besser, deshalb die Richtung.
    leer.improvement = float((a - s) / a) if a > 0 else 0.0

    if leer.improvement < min_improvement:
        leer.reason = (
            f"Vorsprung {leer.improvement:+.1%} unter der Schwelle "
            f"von {min_improvement:.0%}"
        )
        return leer

    leer.promoted = True
    leer.reason = f"Vorsprung {leer.improvement:+.1%} über {len(gemeinsam)} Vorhersagen"
    return leer


def evaluate_shadow(
    session: Session, *, now: datetime | None = None, **kw
) -> list[Comparison]:
    """Prüft alle Schattenmodelle und befördert die, die sich bewährt haben."""
    ergebnisse: list[Comparison] = []
    schatten = session.scalars(
        select(Model).where(Model.status == STATUS_SHADOW)
    ).all()

    for modell in schatten:
        vergleich = compare(session, modell.id, now=now, **kw)
        if vergleich.promoted or vergleich.active_id is None:
            promote(session, modell.id)
            vergleich.promoted = True
            log.info(
                "Modell %d befördert (%s, %d h): %s",
                modell.id,
                vergleich.target,
                vergleich.lead_hours,
                vergleich.reason,
            )
        else:
            log.info(
                "Modell %d bleibt im Schatten (%s, %d h): %s",
                modell.id,
                vergleich.target,
                vergleich.lead_hours,
                vergleich.reason,
            )
        ergebnisse.append(vergleich)
    return ergebnisse
