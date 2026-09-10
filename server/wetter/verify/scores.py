"""Bewertung von Vorhersagen.

Der Teil, der dieses Projekt von einem huebschen Zufallsgenerator unterscheidet. Eine
selbstgebaute Vorhersage ist genau so viel wert, wie man ihre Trefferquote kennt --
und zwar im Vergleich zu etwas, das man umsonst haben koennte.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _paare(vorhersage, beobachtung) -> tuple[np.ndarray, np.ndarray]:
    """Bringt zwei Reihen auf gemeinsame gueltige Werte."""
    p = np.asarray(vorhersage, dtype=float)
    y = np.asarray(beobachtung, dtype=float)
    gueltig = np.isfinite(p) & np.isfinite(y)
    return p[gueltig], y[gueltig]


def brier_score(vorhersage, beobachtung) -> float:
    """Mittlerer quadratischer Fehler einer Wahrscheinlichkeitsvorhersage.

    0 ist perfekt, 0,25 entspricht dem stumpfen "50 %" und 1 ist das denkbar
    schlechteste. Anders als eine Trefferquote bestraft der Brier Score selbstbewusste
    Fehlaussagen staerker als vorsichtige.
    """
    p, y = _paare(vorhersage, beobachtung)
    return float(np.mean((p - y) ** 2)) if len(p) else float("nan")


def brier_skill_score(vorhersage, beobachtung, referenz) -> float:
    """Gewinn gegenueber einer Referenzvorhersage.

    1 ist perfekt, 0 heisst "genauso gut wie die Referenz", negativ heisst schlechter.
    Das ist die eigentlich interessante Zahl: ein Brier Score von 0,15 sagt fuer sich
    genommen nichts, erst der Vergleich mit der Klimatologie macht ihn aussagekraeftig.
    """
    p = np.asarray(vorhersage, dtype=float)
    y = np.asarray(beobachtung, dtype=float)
    r = np.asarray(referenz, dtype=float)
    gueltig = np.isfinite(p) & np.isfinite(y) & np.isfinite(r)
    if not gueltig.any():
        return float("nan")
    bs = np.mean((p[gueltig] - y[gueltig]) ** 2)
    bs_ref = np.mean((r[gueltig] - y[gueltig]) ** 2)
    return float(1.0 - bs / bs_ref) if bs_ref > 0 else float("nan")


def mae(vorhersage, beobachtung) -> float:
    p, y = _paare(vorhersage, beobachtung)
    return float(np.mean(np.abs(p - y))) if len(p) else float("nan")


def rmse(vorhersage, beobachtung) -> float:
    p, y = _paare(vorhersage, beobachtung)
    return float(np.sqrt(np.mean((p - y) ** 2))) if len(p) else float("nan")


def skill_score(vorhersage, beobachtung, referenz, *, metrik=mae) -> float:
    """Allgemeiner Gewinn gegenueber einer Referenz, fuer beliebige Fehlermasse."""
    e = metrik(vorhersage, beobachtung)
    e_ref = metrik(referenz, beobachtung)
    if not np.isfinite(e) or not np.isfinite(e_ref) or e_ref == 0:
        return float("nan")
    return float(1.0 - e / e_ref)


def pinball_loss(vorhersage, beobachtung, quantil: float) -> float:
    """Verlustfunktion der Quantilregression.

    Bestraft Unterschaetzung und Ueberschaetzung unterschiedlich stark, je nach
    Quantil -- genau das bringt ein Modell dazu, ein ehrliches 10-%- statt eines
    mittleren Werts zu liefern.
    """
    p, y = _paare(vorhersage, beobachtung)
    if not len(p):
        return float("nan")
    diff = y - p
    return float(np.mean(np.maximum(quantil * diff, (quantil - 1.0) * diff)))


def crps_from_quantiles(quantile: dict[float, np.ndarray], beobachtung) -> float:
    """Naeherung des CRPS aus einer Handvoll Quantilvorhersagen.

    Der CRPS bewertet eine ganze Vorhersageverteilung statt nur eines Werts und ist
    damit das richtige Mass fuer die Temperaturbaender. Aus diskreten Quantilen
    ergibt er sich als Mittel der Pinball-Verluste, mal zwei.
    """
    if not quantile:
        return float("nan")
    verluste = [pinball_loss(v, beobachtung, q) for q, v in sorted(quantile.items())]
    gueltig = [v for v in verluste if np.isfinite(v)]
    return float(2.0 * np.mean(gueltig)) if gueltig else float("nan")


@dataclass
class ReliabilityBin:
    """Ein Balken des Zuverlaessigkeitsdiagramms."""

    lower: float
    upper: float
    mean_forecast: float
    observed_frequency: float
    count: int


def reliability(vorhersage, beobachtung, *, bins: int = 10) -> list[ReliabilityBin]:
    """Zuverlaessigkeitsdiagramm: sagt "70 %" auch in 70 % der Faelle Regen?

    Ein perfekt kalibriertes Modell liegt auf der Diagonalen. Systematisch darunter
    heisst: das Modell ist zu selbstbewusst. Das ist der Teil, den man in der PWA
    sehen will -- er zeigt nicht nur *ob*, sondern *wo* das Modell danebenliegt.
    """
    p, y = _paare(vorhersage, beobachtung)
    if not len(p):
        return []
    kanten = np.linspace(0.0, 1.0, bins + 1)
    eimer = np.clip(np.digitize(p, kanten[1:-1]), 0, bins - 1)

    out: list[ReliabilityBin] = []
    for b in range(bins):
        maske = eimer == b
        anzahl = int(maske.sum())
        out.append(
            ReliabilityBin(
                lower=float(kanten[b]),
                upper=float(kanten[b + 1]),
                mean_forecast=float(p[maske].mean()) if anzahl else float("nan"),
                observed_frequency=float(y[maske].mean()) if anzahl else float("nan"),
                count=anzahl,
            )
        )
    return out


def calibration_error(vorhersage, beobachtung, *, bins: int = 10) -> float:
    """Mittlere Abweichung von der Diagonalen, gewichtet nach Fallzahl."""
    balken = [b for b in reliability(vorhersage, beobachtung, bins=bins) if b.count]
    if not balken:
        return float("nan")
    gesamt = sum(b.count for b in balken)
    return float(
        sum(
            b.count * abs(b.mean_forecast - b.observed_frequency) for b in balken
        )
        / gesamt
    )


def coverage(untere, obere, beobachtung) -> float:
    """Anteil der Beobachtungen innerhalb eines Vorhersagebands.

    Ein 10-bis-90-Prozent-Band sollte rund 80 % der Faelle einschliessen. Deutlich
    weniger heisst: das Band ist zu schmal und die angezeigte Unsicherheit gelogen.
    """
    u = np.asarray(untere, dtype=float)
    o = np.asarray(obere, dtype=float)
    y = np.asarray(beobachtung, dtype=float)
    gueltig = np.isfinite(u) & np.isfinite(o) & np.isfinite(y)
    if not gueltig.any():
        return float("nan")
    drin = (y[gueltig] >= u[gueltig]) & (y[gueltig] <= o[gueltig])
    return float(drin.mean())
