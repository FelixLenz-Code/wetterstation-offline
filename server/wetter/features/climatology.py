"""Klimatologie: was an einem Kalendertag zu einer Tageszeit normal ist.

Zwei Aufgaben. Erstens als *Merkmal*: "14 degC" sagt einem Modell wenig, "3 K waermer
als an einem 10. September um diese Uhrzeit ueblich" dagegen viel. Zweitens als
*Baseline*: die Klimatologie ist die Vorhersage "es wird wie immer um diese
Jahreszeit". Jedes gelernte Modell muss sie schlagen, sonst hat es nichts gelernt.

Die Normale ist bewusst zweidimensional (Kalendertag x Tagesstunde). Ein reiner
Tagesmittelwert waere als Bezug untauglich: gegen ihn gemessen ist *jede* Nachtstunde
zu kalt und *jeder* Nachmittag zu warm -- die Abweichung wuerde dann den Tagesgang
messen statt die Wetterlage.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

#: Halbe Fensterbreite in Tagen fuer die Glaettung ueber das Jahr.
SMOOTH_DAYS = 7

#: Tage im Jahr (Schaltjahr), damit der 29. Februar einen Platz hat.
DAYS = 366

HOURS = 24


def _smooth_yearly(values: np.ndarray, half_width: int = SMOOTH_DAYS) -> np.ndarray:
    """Glaettet ueber die Tagesachse zyklisch.

    Zyklisch, weil der 31. Dezember und der 1. Januar Nachbarn sind -- ohne das
    haette die Kurve genau zum Jahreswechsel einen Sprung. ``values`` darf ein- oder
    zweidimensional sein; geglaettet wird immer die erste Achse.
    """
    eindim = values.ndim == 1
    arr = values[:, None] if eindim else values
    n = arr.shape[0]
    breite = 2 * half_width + 1

    erweitert = np.concatenate([arr[-half_width:], arr, arr[:half_width]], axis=0)
    maske = np.isfinite(erweitert)
    gefuellt = np.where(maske, erweitert, 0.0)

    # Gleitendes Fenster ueber die Tagesachse, Luecken zaehlen nicht mit.
    kern = np.ones(breite)
    summe = np.apply_along_axis(lambda c: np.convolve(c, kern, "same"), 0, gefuellt)
    gewicht = np.apply_along_axis(
        lambda c: np.convolve(c, kern, "same"), 0, maske.astype(float)
    )
    out = np.divide(summe, gewicht, out=np.full_like(summe, np.nan), where=gewicht > 0)
    out = out[half_width : half_width + n]
    return out[:, 0] if eindim else out


def _cell_mean(values: np.ndarray, day: np.ndarray, hour: np.ndarray) -> np.ndarray:
    """Mittelwert je (Tag, Stunde)-Zelle, anschliessend ueber die Tage geglaettet."""
    out = np.full((DAYS, HOURS), np.nan)
    gueltig = np.isfinite(values)
    if not gueltig.any():
        return out
    flach = (day[gueltig] - 1) * HOURS + hour[gueltig]
    summe = np.bincount(flach, values[gueltig], minlength=DAYS * HOURS)
    anzahl = np.bincount(flach, minlength=DAYS * HOURS)
    with np.errstate(invalid="ignore"):
        mittel = np.divide(
            summe, anzahl, out=np.full(DAYS * HOURS, np.nan), where=anzahl > 0
        )
    return _smooth_yearly(mittel.reshape(DAYS, HOURS))


def _daily_mean(values: np.ndarray, day: np.ndarray) -> np.ndarray:
    out = np.full(DAYS, np.nan)
    gueltig = np.isfinite(values)
    if gueltig.any():
        summe = np.bincount(day[gueltig] - 1, values[gueltig], minlength=DAYS)
        anzahl = np.bincount(day[gueltig] - 1, minlength=DAYS)
        with np.errstate(invalid="ignore"):
            out = np.divide(summe, anzahl, out=out, where=anzahl > 0)
    return _smooth_yearly(out)


@dataclass
class Climatology:
    """Normale ueber das Jahr, aus einer langen Messreihe gewonnen."""

    temp_mean: np.ndarray
    """Mittlere Temperatur je (Tag des Jahres, Stunde UTC), Form ``(366, 24)``."""

    temp_min: np.ndarray
    """Mittleres Tagesminimum je Tag des Jahres, Laenge 366."""

    temp_max: np.ndarray
    """Mittleres Tagesmaximum je Tag des Jahres, Laenge 366."""

    rain_probability: np.ndarray
    """Anteil der Stunden mit Niederschlag je (Tag, Stunde), Form ``(366, 24)``."""

    years: float
    """Laenge der zugrundeliegenden Reihe in Jahren -- fuer die Belastbarkeit."""

    @classmethod
    def from_hourly(
        cls, frame: pd.DataFrame, *, rain_threshold_mm: float = 0.1
    ) -> Climatology:
        """Berechnet die Normale aus einer Stundentabelle im kanonischen Schema."""
        if "temperature_c" not in frame.columns:
            raise ValueError("Klimatologie braucht mindestens temperature_c")

        day = frame.index.dayofyear.to_numpy()
        hour = frame.index.hour.to_numpy()
        temp = frame["temperature_c"].to_numpy(dtype=float)

        tages_index = frame.index.floor("D")
        tages_min = frame["temperature_c"].groupby(tages_index).min()
        tages_max = frame["temperature_c"].groupby(tages_index).max()

        if "precip_mm" in frame.columns:
            roh = frame["precip_mm"].to_numpy(dtype=float)
            nass = (roh >= rain_threshold_mm).astype(float)
            regen = np.where(np.isnan(roh), np.nan, nass)
        else:
            regen = np.full(len(frame), np.nan)

        spanne = (frame.index.max() - frame.index.min()).days / 365.25
        return cls(
            temp_mean=_cell_mean(temp, day, hour),
            temp_min=_daily_mean(
                tages_min.to_numpy(dtype=float), tages_min.index.dayofyear.to_numpy()
            ),
            temp_max=_daily_mean(
                tages_max.to_numpy(dtype=float), tages_max.index.dayofyear.to_numpy()
            ),
            rain_probability=_cell_mean(regen, day, hour),
            years=float(spanne),
        )

    def _lookup_2d(self, table: np.ndarray, index: pd.DatetimeIndex) -> np.ndarray:
        return table[index.dayofyear.to_numpy() - 1, index.hour.to_numpy()]

    def _lookup_1d(self, table: np.ndarray, index: pd.DatetimeIndex) -> np.ndarray:
        return table[index.dayofyear.to_numpy() - 1]

    def normal_temp(self, index: pd.DatetimeIndex) -> np.ndarray:
        """Normaltemperatur fuer Kalendertag *und* Tagesstunde."""
        return self._lookup_2d(self.temp_mean, index)

    def normal_temp_min(self, index: pd.DatetimeIndex) -> np.ndarray:
        return self._lookup_1d(self.temp_min, index)

    def normal_temp_max(self, index: pd.DatetimeIndex) -> np.ndarray:
        return self._lookup_1d(self.temp_max, index)

    def normal_rain_probability(self, index: pd.DatetimeIndex) -> np.ndarray:
        return self._lookup_2d(self.rain_probability, index)

    def to_dict(self) -> dict:
        """Serialisierung fuer die Ablage im Modell-Register."""
        return {
            "temp_mean": self.temp_mean.tolist(),
            "temp_min": self.temp_min.tolist(),
            "temp_max": self.temp_max.tolist(),
            "rain_probability": self.rain_probability.tolist(),
            "years": self.years,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Climatology:
        return cls(
            temp_mean=np.asarray(data["temp_mean"], dtype=float),
            temp_min=np.asarray(data["temp_min"], dtype=float),
            temp_max=np.asarray(data["temp_max"], dtype=float),
            rain_probability=np.asarray(data["rain_probability"], dtype=float),
            years=float(data["years"]),
        )

    @classmethod
    def empty(cls) -> Climatology:
        """Leere Normale -- fuer Merkmalsnamen und Tests."""
        return cls(
            temp_mean=np.zeros((DAYS, HOURS)),
            temp_min=np.zeros(DAYS),
            temp_max=np.zeros(DAYS),
            rain_probability=np.zeros((DAYS, HOURS)),
            years=0.0,
        )
