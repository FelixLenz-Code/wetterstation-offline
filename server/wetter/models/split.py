"""Zeitliche Aufteilung der Daten in Training, Kalibrierung und Test.

Warum das ein eigenes Modul ist: bei Zeitreihen ist eine zufaellige Aufteilung ein
schwerer Fehler. Zwei aufeinanderfolgende Stunden sind fast identisch -- landet die
eine im Training und die andere im Test, misst man auswendig Gelerntes und bekommt
grossartige Zahlen, die im Betrieb nichts wert sind.

Deshalb wird strikt nach Zeit geschnitten, und zwischen den Abschnitten liegt eine
Luecke, die mindestens so gross ist wie die laengste Vorlaufzeit. Ohne diese Luecke
wuerde ein Trainingsziel aus dem Fenster ``(t, t+48h]`` in den Testzeitraum
hineinreichen.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    """Drei Abschnitte in zeitlicher Reihenfolge."""

    train_end: pd.Timestamp
    """Letzter Zeitpunkt des Trainings (ausschliesslich)."""

    calib_end: pd.Timestamp
    """Letzter Zeitpunkt der Kalibrierung (ausschliesslich)."""

    gap_hours: int = 48
    """Luecke zwischen den Abschnitten, mindestens die laengste Vorlaufzeit."""

    @classmethod
    def by_fraction(
        cls,
        index: pd.DatetimeIndex,
        *,
        train: float = 0.7,
        calib: float = 0.15,
        gap_hours: int = 48,
    ) -> TimeSplit:
        """Teilt nach Anteilen der Zeitachse.

        Der Rest nach Training und Kalibrierung ist der Test -- der Abschnitt, den
        kein Modell und keine Kalibrierung je gesehen hat.
        """
        start, ende = index.min(), index.max()
        spanne = ende - start
        return cls(
            train_end=start + spanne * train,
            calib_end=start + spanne * (train + calib),
            gap_hours=gap_hours,
        )

    def masks(self, index: pd.DatetimeIndex) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Liefert die drei Masken (Training, Kalibrierung, Test)."""
        lueck = pd.Timedelta(hours=self.gap_hours)
        train = index < self.train_end
        calib = (index >= self.train_end + lueck) & (index < self.calib_end)
        test = index >= self.calib_end + lueck
        return (
            pd.Series(train, index=index),
            pd.Series(calib, index=index),
            pd.Series(test, index=index),
        )

    def describe(self, index: pd.DatetimeIndex) -> str:
        tr, ca, te = self.masks(index)
        def spanne(maske: pd.Series) -> str:
            gewaehlt = index[maske.to_numpy()]
            if not len(gewaehlt):
                return "leer"
            n = f"{len(gewaehlt):,}".replace(",", ".")
            return f"{gewaehlt.min():%Y-%m-%d} bis {gewaehlt.max():%Y-%m-%d} ({n} h)"
        return (
            f"  Training     {spanne(tr)}\n"
            f"  Kalibrierung {spanne(ca)}\n"
            f"  Test         {spanne(te)}"
        )
