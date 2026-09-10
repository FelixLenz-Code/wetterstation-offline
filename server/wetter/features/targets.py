"""Vorhersageziele aus einer Stundentabelle.

Getrennt vom Merkmalsbau, weil hier in die *Zukunft* geschaut wird. Beim Training ist
das erlaubt, bei der Inferenz nicht -- die Trennung macht es schwer, versehentlich ein
Ziel als Merkmal einzuschleusen und sich damit selbst zu betruegen.

Zeitkonvention durchgehend: ein Ziel mit Vorlaufzeit ``h`` zum Zeitpunkt ``t``
beschreibt das Fenster ``(t, t+h]``, also ausschliesslich Zukunft.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: Ab dieser Stundensumme gilt eine Stunde als "Regen".
RAIN_THRESHOLD_MM = 0.1

#: Vorlaufzeiten der Regenvorhersage in Stunden.
RAIN_LEADS: tuple[int, ...] = (6, 12, 18, 24)

#: Vorlaufzeiten der Temperaturvorhersage in Stunden.
TEMP_LEADS: tuple[int, ...] = (6, 12, 18, 24, 30, 36, 42, 48)


def rain_in_next(precip: pd.Series, hours: int) -> pd.Series:
    """1, wenn im Fenster ``(t, t+h]`` mindestens eine Regenstunde liegt.

    ``NaN``, wenn das Fenster nicht vollstaendig bekannt ist -- am Reihenende oder bei
    Messluecken. Eine Luecke als "kein Regen" zu werten waere der klassische Weg,
    sich eine zu gute Trefferquote anzutrainieren.
    """
    nass = (precip >= RAIN_THRESHOLD_MM).astype(float)
    nass[precip.isna()] = np.nan
    # shift(-1) verschiebt das Fenster in die Zukunft,
    # min_periods erzwingt Vollstaendigkeit.
    fenster = nass.shift(-1).rolling(hours, min_periods=hours).max()
    return fenster.shift(-(hours - 1))


def value_at(series: pd.Series, hours: int) -> pd.Series:
    """Wert genau ``h`` Stunden spaeter."""
    return series.shift(-hours)


def extreme_in_next(series: pd.Series, hours: int, *, how: str = "min") -> pd.Series:
    """Minimum oder Maximum im Fenster ``(t, t+h]``."""
    verschoben = series.shift(-1)
    roll = verschoben.rolling(hours, min_periods=hours)
    fenster = roll.min() if how == "min" else roll.max()
    return fenster.shift(-(hours - 1))


def build_targets(
    frame: pd.DataFrame,
    *,
    rain_leads: tuple[int, ...] = RAIN_LEADS,
    temp_leads: tuple[int, ...] = TEMP_LEADS,
) -> pd.DataFrame:
    """Baut alle Ziele zu einer Tabelle.

    Erwartet die Stundentabelle im kanonischen Schema. Spalten, deren Quelle fehlt,
    entfallen -- ohne Niederschlagsmessung gibt es eben kein Regenziel.
    """
    out = pd.DataFrame(index=frame.index)

    if "precip_mm" in frame.columns:
        regen = pd.to_numeric(frame["precip_mm"], errors="coerce")
        for h in rain_leads:
            out[f"rain_next_{h}h"] = rain_in_next(regen, h)
            # Menge als Nebenwert -- fuer die Anzeige, nicht als Klassifikationsziel.
            out[f"rain_amount_{h}h"] = (
                regen.shift(-1).rolling(h, min_periods=h).sum().shift(-(h - 1))
            )

    if "temperature_c" in frame.columns:
        temp = pd.to_numeric(frame["temperature_c"], errors="coerce")
        for h in temp_leads:
            out[f"temp_at_{h}h"] = value_at(temp, h)
        for h in (24, 48):
            out[f"temp_min_{h}h"] = extreme_in_next(temp, h, how="min")
            out[f"temp_max_{h}h"] = extreme_in_next(temp, h, how="max")

    if "wind_gust_ms" in frame.columns or "wind_speed_ms" in frame.columns:
        quelle = "wind_gust_ms" if "wind_gust_ms" in frame.columns else "wind_speed_ms"
        boe = pd.to_numeric(frame[quelle], errors="coerce")
        for h in (6, 12, 24):
            out[f"gust_max_{h}h"] = extreme_in_next(boe, h, how="max")

    return out


def target_names(frame_columns: list[str]) -> list[str]:
    """Welche Ziele aus einem gegebenen Spaltensatz entstehen."""
    idx = pd.date_range("2024-01-01", periods=120, freq="h", tz="UTC")
    leer = pd.DataFrame(
        {c: np.zeros(len(idx)) for c in frame_columns}, index=idx
    )
    return list(build_targets(leer).columns)
