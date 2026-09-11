"""Bias-Korrektur zwischen eigener Station und DWD-Referenz.

Stufe zwei der Übertragung. Stufe eins ist die physikalische Angleichung (Druck auf
Meeresniveau, Wind auf 10 m) und steckt in :mod:`wetter.features.meteo`. Was danach
noch übrig bleibt, ist der Aufstellungseffekt: der Strahlungsschutz ist ein anderer,
die Station hängt an einer Hauswand statt auf einer freien Wiese, der Regenmesser
steht im Windschatten.

Solche Unterschiede sind keine Zufallsfehler, sondern systematisch und
wertabhängig -- ein Thermometer an der Hauswand liest tagsüber deutlich zu warm und
nachts kaum. Ein einfacher Mittelwertversatz träfe deshalb daneben. Die
Quantil-Abbildung korrigiert stattdessen entlang der ganzen Verteilung: der zehnte
Prozentwert der eigenen Reihe wird auf den zehnten der DWD-Reihe gelegt und so fort.

Wichtig ist, was die Korrektur *nicht* kann: sie gleicht Verteilungen an, nicht
einzelne Stunden. Sie ersetzt keine ordentliche Aufstellung, und sie macht aus einem
kaputten Sensor keinen guten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

#: Stützstellen der Abbildung. 21 Punkte in Fünf-Prozent-Schritten reichen: mehr
#: bildet vor allem das Rauschen der Stichprobe ab.
QUANTILES: tuple[float, ...] = tuple(np.round(np.arange(0.0, 1.001, 0.05), 3))

#: Unter dieser Zahl gemeinsamer Stunden wird gar nicht korrigiert. Vier Wochen
#: stündlicher Daten sind rund 670 Stunden -- darunter beschreibt die Abbildung die
#: Wetterlage dieser Wochen statt des Aufstellungsunterschieds.
MIN_SAMPLES = 600

#: Ab dieser Zahl gemeinsamer Stunden wird die volle Quantil-Abbildung genutzt,
#: darunter nur ein konstanter Versatz.
#:
#: Der Grund ist die Jahreszeit. Eine Quantil-Abbildung verformt die Verteilung
#: entlang ihrer ganzen Breite. Passt man sie auf zwei Sommermonaten an, hat sie
#: nie einen Frostwert gesehen -- und rechnet im Winter Werte zurecht, für die sie
#: keine Grundlage hat. Ein konstanter Versatz kann das nicht: er verschiebt, ohne
#: die Form anzufassen, und liegt im schlimmsten Fall um den Betrag daneben, um den
#: sich der Aufstellungsfehler über das Jahr ändert.
#:
#: 8000 Stunden sind knapp ein Jahr, also mindestens ein voller Jahresgang.
MIN_SAMPLES_FULL = 8000


@dataclass
class QuantileMapping:
    """Abbildung eigener Messwerte auf die DWD-Verteilung."""

    column: str
    own_quantiles: np.ndarray
    reference_quantiles: np.ndarray
    samples: int
    quantiles: tuple[float, ...] = field(default=QUANTILES)

    def apply(self, values) -> np.ndarray:
        """Rechnet eigene Messwerte auf die Referenzskala um.

        Zwischen den Stützstellen wird linear interpoliert. Ausserhalb des gelernten
        Bereichs wird der Versatz des äussersten Stützpunkts fortgeschrieben statt
        gekappt -- ein Rekordwert soll korrigiert, nicht abgeschnitten werden.
        """
        x = np.asarray(values, dtype=float)
        out = np.interp(x, self.own_quantiles, self.reference_quantiles)

        unten = x < self.own_quantiles[0]
        oben = x > self.own_quantiles[-1]
        if unten.any():
            versatz = self.reference_quantiles[0] - self.own_quantiles[0]
            out = np.where(unten, x + versatz, out)
        if oben.any():
            versatz = self.reference_quantiles[-1] - self.own_quantiles[-1]
            out = np.where(oben, x + versatz, out)
        return np.where(np.isnan(x), np.nan, out)

    def inverse(self, values) -> np.ndarray:
        """Rechnet von der Referenzskala zurück auf die eigene Station.

        Gebraucht für die *Ausgabe* der Modelle, und das ist kein Detail: die
        Modelle sind auf DWD-Daten trainiert und denken in DWD-Werten. Korrigiert man
        nur die Eingabe, kommt auch die Vorhersage in DWD-Werten heraus -- verglichen
        und angezeigt wird sie aber neben den Rohwerten der eigenen Station.

        Nachgemessen an einem vollen Durchlauf mit 0,8 K Aufstellungsversatz: nur die
        Eingabe zu korrigieren machte den mittleren Temperaturfehler auf sechs Stunden
        *schlechter* (2,32 auf 2,49 K), weil die Vorhersage dann systematisch um den
        Versatz neben der Beobachtung lag.
        """
        x = np.asarray(values, dtype=float)
        out = np.interp(x, self.reference_quantiles, self.own_quantiles)

        unten = x < self.reference_quantiles[0]
        oben = x > self.reference_quantiles[-1]
        if unten.any():
            versatz = self.own_quantiles[0] - self.reference_quantiles[0]
            out = np.where(unten, x + versatz, out)
        if oben.any():
            versatz = self.own_quantiles[-1] - self.reference_quantiles[-1]
            out = np.where(oben, x + versatz, out)
        return np.where(np.isnan(x), np.nan, out)

    @property
    def median_shift(self) -> float:
        """Versatz in der Mitte der Verteilung -- die Zahl fürs Logbuch."""
        mitte = len(self.quantiles) // 2
        return float(self.reference_quantiles[mitte] - self.own_quantiles[mitte])

    def to_dict(self) -> dict:
        return {
            "column": self.column,
            "own_quantiles": self.own_quantiles.tolist(),
            "reference_quantiles": self.reference_quantiles.tolist(),
            "samples": self.samples,
            "quantiles": list(self.quantiles),
        }

    @classmethod
    def from_dict(cls, data: dict) -> QuantileMapping:
        return cls(
            column=data["column"],
            own_quantiles=np.asarray(data["own_quantiles"], dtype=float),
            reference_quantiles=np.asarray(data["reference_quantiles"], dtype=float),
            samples=int(data["samples"]),
            quantiles=tuple(data["quantiles"]),
        )


def fit_mapping(
    own: pd.Series,
    reference: pd.Series,
    *,
    column: str,
    quantiles: tuple[float, ...] = QUANTILES,
    min_samples: int = MIN_SAMPLES,
) -> QuantileMapping | None:
    """Lernt die Abbildung aus zeitgleichen Messungen beider Reihen.

    Verglichen werden nur Stunden, in denen **beide** Reihen einen Wert haben. Sonst
    würde ein Ausfall der eigenen Station als Verteilungsunterschied durchgehen.

    Gibt ``None`` zurück, wenn zu wenige gemeinsame Stunden vorliegen oder die eigene
    Reihe keine Streuung zeigt -- eine Abbildung aus einem konstanten Wert wäre
    sinnlos und würde alles auf eine Zahl ziehen.
    """
    gemeinsam = pd.concat([own.rename("own"), reference.rename("ref")], axis=1).dropna()
    if len(gemeinsam) < min_samples:
        log.info(
            "%s: nur %d gemeinsame Stunden, keine Bias-Korrektur (mindestens %d)",
            column,
            len(gemeinsam),
            min_samples,
        )
        return None

    eigene = np.quantile(gemeinsam["own"].to_numpy(), quantiles)
    referenz = np.quantile(gemeinsam["ref"].to_numpy(), quantiles)

    if len(gemeinsam) < MIN_SAMPLES_FULL:
        # Zu kurz für eine formverändernde Abbildung: nur verschieben. Der Median
        # der Einzelabweichungen ist robuster als die Differenz der Mittelwerte --
        # ein einzelner Ausreisser soll die Korrektur nicht mitziehen.
        versatz = float(np.median(gemeinsam["ref"] - gemeinsam["own"]))
        referenz = eigene + versatz
        log.info(
            "%s: nur %d gemeinsame Stunden -- konstanter Versatz %+.2f statt "
            "voller Quantil-Abbildung",
            column,
            len(gemeinsam),
            versatz,
        )

    if not np.all(np.diff(eigene) > 0) or not np.all(np.diff(referenz) > 0):
        # np.interp braucht streng steigende Stützstellen -- und zwar auf *beiden*
        # Seiten, weil die Abbildung auch rückwärts genutzt wird. Wiederholte Werte
        # treten bei grob quantisierten Größen auf (Bewölkung in Achteln etwa); dann
        # lieber nicht korrigieren, als eine Abbildung zu bauen, die Werte
        # verschluckt oder sich nicht umkehren lässt.
        log.warning("%s: Stützstellen nicht streng steigend, keine Korrektur", column)
        return None

    abbildung = QuantileMapping(
        column=column,
        own_quantiles=eigene,
        reference_quantiles=referenz,
        samples=len(gemeinsam),
        quantiles=quantiles,
    )
    log.info(
        "%s: Bias-Korrektur aus %d Stunden, Versatz in der Mitte %+.2f",
        column,
        len(gemeinsam),
        abbildung.median_shift,
    )
    return abbildung


def fit_all(
    own: pd.DataFrame,
    reference: pd.DataFrame,
    *,
    columns: list[str] | None = None,
    **kw,
) -> dict[str, QuantileMapping]:
    """Lernt Abbildungen für alle gemeinsamen Spalten."""
    spalten = columns or [c for c in own.columns if c in reference.columns]
    out: dict[str, QuantileMapping] = {}
    for c in spalten:
        abbildung = fit_mapping(own[c], reference[c], column=c, **kw)
        if abbildung is not None:
            out[c] = abbildung
    return out


def apply_all(frame: pd.DataFrame, mappings: dict[str, QuantileMapping]) -> pd.DataFrame:
    """Wendet gelernte Abbildungen auf eine Messreihe an."""
    out = frame.copy()
    for spalte, abbildung in mappings.items():
        if spalte in out.columns:
            out[spalte] = abbildung.apply(out[spalte])
    return out
