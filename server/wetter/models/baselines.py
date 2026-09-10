"""Baselines -- die Messlatte, die jedes gelernte Modell schlagen muss.

Ohne sie ist jede Trefferquote bedeutungslos. "70 % richtig" klingt gut, ist aber
wertlos, wenn schon "sagt immer nein" auf 75 % kommt. Drei Verfahren:

* **Persistenz** -- es bleibt, wie es ist. Auf kurze Sicht erstaunlich stark und der
  eigentliche Gegner bei Vorlaufzeiten bis etwa sechs Stunden.
* **Klimatologie** -- es wird wie immer um diese Jahreszeit. Der Gegner auf lange
  Sicht; ab etwa 48 Stunden schlaegt sie fast alles.
* **Zambretti** -- die klassische Faustformel aus Luftdruck, Drucktendenz,
  Windrichtung und Jahreszeit, wie sie in mechanischen Wettergläsern steckt. Genau
  das Verfahren, das dieses Projekt schlagen will.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from wetter.features.climatology import Climatology

#: Grenze in hPa je 3 Stunden, ab der der Druck als steigend/fallend gilt.
TREND_THRESHOLD_HPA = 0.5

#: Druckkorrektur in hPa nach Windrichtung (16 Sektoren ab Nord, im Uhrzeigersinn).
#: Nordwind gilt als wetterberuhigend, Suedwind als wetterverschlechternd.
WIND_CORRECTION_HPA = np.array(
    [6, 5, 5, 2, -1, -3, -4, -5, -6, -5, -3, 0, 2, 4, 5, 6], dtype=float
)


def pressure_trend(delta_3h) -> np.ndarray:
    """Klassifiziert die Drucktendenz: -1 fallend, 0 gleichbleibend, +1 steigend."""
    d = np.asarray(delta_3h, dtype=float)
    out = np.zeros_like(d)
    out[d > TREND_THRESHOLD_HPA] = 1.0
    out[d < -TREND_THRESHOLD_HPA] = -1.0
    return np.where(np.isnan(d), np.nan, out)


def zambretti_z(pressure_sea_hpa, delta_3h, month, wind_dir_deg=None) -> np.ndarray:
    """Berechnet die Zambretti-Zahl (1 bis 32; klein = bestaendig, gross = Sturm).

    Die drei Geradengleichungen je Tendenz sind der Kern des Verfahrens und
    unstrittig. Bei Wind- und Jahreszeitkorrektur kursieren leicht abweichende
    Fassungen -- hier steht die gaengige additive Variante.

    Wichtig fuer die Fairness des Vergleichs: die Zahl wird *nicht* ueber die
    ueberlieferte Texttabelle in eine Wahrscheinlichkeit uebersetzt. Stattdessen
    lernt :class:`ZambrettiBaseline` den Zusammenhang zwischen Zahl und tatsaechlicher
    Regenhaeufigkeit aus denselben Trainingsdaten, die auch das ML-Modell sieht. Das
    macht Zambretti zu einem ernstzunehmenden Gegner statt zu einem Strohmann.
    """
    p = np.asarray(pressure_sea_hpa, dtype=float).copy()
    trend = pressure_trend(delta_3h)
    m = np.asarray(month, dtype=float)

    if wind_dir_deg is not None:
        richtung = np.asarray(wind_dir_deg, dtype=float)
        fehlt = np.isnan(richtung)
        # NaN vor der Umwandlung in einen Index ersetzen -- astype(int) auf NaN ist
        # undefiniert und liefert je nach Plattform Muell statt eines Fehlers.
        sicher = np.where(fehlt, 0.0, richtung)
        sektor = np.floor((sicher % 360.0) / 22.5 + 0.5).astype(int) % 16
        p = p + np.where(fehlt, 0.0, WIND_CORRECTION_HPA[sektor])

    # Jahreszeit: im Sommerhalbjahr verstaerkt die Tendenz ihre Wirkung.
    sommer = (m >= 4) & (m <= 9)
    p = p + np.where(sommer & (trend > 0), 7.0, 0.0)
    p = p - np.where(sommer & (trend < 0), 7.0, 0.0)

    z = np.where(
        trend > 0,
        185.0 - 0.16 * p,
        np.where(trend < 0, 127.0 - 0.12 * p, 144.0 - 0.13 * p),
    )
    return np.where(np.isnan(trend) | np.isnan(p), np.nan, np.clip(z, 1.0, 32.0))


class Baseline:
    """Gemeinsame Schnittstelle aller Baselines."""

    name: str = "baseline"

    def predict_rain(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        raise NotImplementedError

    def predict_temp(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        raise NotImplementedError


class PersistenceBaseline(Baseline):
    """Es bleibt, wie es gerade ist.

    Fuer Regen: regnet es jetzt, wird Regen vorhergesagt. Als Wahrscheinlichkeit
    ausgedrueckt waeren das 0 oder 1 -- das bestraft der Brier Score hart und zu
    Recht. Deshalb wird stattdessen die aus den Trainingsdaten gelernte Haeufigkeit
    genutzt: "wie oft regnete es in den naechsten h Stunden, wenn es jetzt regnet?"
    """

    name = "persistenz"

    def __init__(self) -> None:
        self._rain_rates: dict[tuple[int, bool], float] = {}

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> PersistenceBaseline:
        regnet_jetzt = features["precip_sum_1h"].to_numpy(dtype=float) >= 0.1
        for spalte in targets.columns:
            if not spalte.startswith("rain_next_"):
                continue
            lead = int(spalte.removeprefix("rain_next_").removesuffix("h"))
            y = targets[spalte].to_numpy(dtype=float)
            for zustand in (True, False):
                maske = (regnet_jetzt == zustand) & np.isfinite(y)
                self._rain_rates[(lead, zustand)] = (
                    float(y[maske].mean()) if maske.any() else np.nan
                )
        return self

    def predict_rain(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        regnet = features["precip_sum_1h"].to_numpy(dtype=float) >= 0.1
        nass = self._rain_rates.get((lead_hours, True), np.nan)
        trocken = self._rain_rates.get((lead_hours, False), np.nan)
        return np.where(regnet, nass, trocken)

    def predict_temp(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        """Die Temperatur von jetzt.

        Bei Vorlaufzeiten ueber ein paar Stunden ist das absichtlich naiv: die
        Persistenz ignoriert den Tagesgang und wird dadurch zum leichten Gegner --
        genau deshalb gibt es zusaetzlich die Klimatologie.
        """
        return features["temperature"].to_numpy(dtype=float)


class ClimatologyBaseline(Baseline):
    """Es wird wie immer um diese Jahreszeit und Uhrzeit."""

    name = "klimatologie"

    def __init__(self, climatology: Climatology) -> None:
        self.climatology = climatology
        self._rain_tables: dict[int, np.ndarray] = {}

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> ClimatologyBaseline:
        """Lernt die Regenhaeufigkeit je Vorlaufzeit direkt aus den Zielen.

        Der naheliegende Weg -- die stuendliche Regenwahrscheinlichkeit ueber das
        Fenster zu verketten -- ist falsch, und zwar deutlich. Er unterstellt, dass
        aufeinanderfolgende Stunden unabhaengig sind. Regen kommt aber in Bloecken:
        regnet es um 14 Uhr, regnet es um 15 Uhr sehr wahrscheinlich auch. Ueber 24
        Stunden verkettet ergibt die Annahme eine Quasi-Gewissheit fuer Regen und
        damit einen Brier Score *schlechter* als blindes Raten der Grundrate.

        Eine Baseline, die man kuenstlich schlecht macht, schmeichelt dem eigenen
        Modell -- deshalb wird hier die tatsaechliche Haeufigkeit des Zielereignisses
        je Kalendertag und Uhrzeit gelernt, geglaettet wie die uebrige Klimatologie.
        """
        from wetter.features.climatology import _cell_mean

        day = features.index.dayofyear.to_numpy()
        hour = features.index.hour.to_numpy()
        for spalte in targets.columns:
            if not spalte.startswith("rain_next_"):
                continue
            lead = int(spalte.removeprefix("rain_next_").removesuffix("h"))
            self._rain_tables[lead] = _cell_mean(
                targets[spalte].to_numpy(dtype=float), day, hour
            )
        return self

    def predict_rain(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        """Regenwahrscheinlichkeit im Fenster nach der gelernten Normale."""
        tabelle = self._rain_tables.get(lead_hours)
        if tabelle is None:
            raise RuntimeError(
                "ClimatologyBaseline.fit() muss vor predict_rain() laufen"
            )
        idx = features.index
        return tabelle[idx.dayofyear.to_numpy() - 1, idx.hour.to_numpy()]

    def predict_temp(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        ziel = features.index + pd.Timedelta(hours=lead_hours)
        return self.climatology.normal_temp(ziel)


@dataclass
class ZambrettiBaseline(Baseline):
    """Die klassische Faustformel, empirisch auf Wahrscheinlichkeiten geeicht."""

    name: str = "zambretti"
    bins: int = 16
    _tables: dict[int, np.ndarray] = field(default_factory=dict)
    _edges: np.ndarray | None = None

    def z_values(self, features: pd.DataFrame) -> np.ndarray:
        wind = (
            features["wind_dir_deg"].to_numpy(dtype=float)
            if "wind_dir_deg" in features.columns
            else None
        )
        if wind is None and {"wind_u", "wind_v"} <= set(features.columns):
            from wetter.features.meteo import wind_direction

            wind = wind_direction(features["wind_u"], features["wind_v"])
        return zambretti_z(
            features["pressure"].to_numpy(dtype=float),
            features["pressure_delta_3h"].to_numpy(dtype=float),
            features.index.month.to_numpy(dtype=float),
            wind,
        )

    def fit(self, features: pd.DataFrame, targets: pd.DataFrame) -> ZambrettiBaseline:
        z = self.z_values(features)
        self._edges = np.linspace(1.0, 32.0, self.bins + 1)
        eimer = np.digitize(z, self._edges[1:-1])

        for spalte in targets.columns:
            if not spalte.startswith("rain_next_"):
                continue
            lead = int(spalte.removeprefix("rain_next_").removesuffix("h"))
            y = targets[spalte].to_numpy(dtype=float)
            tabelle = np.full(self.bins, np.nan)
            gueltig = np.isfinite(y) & np.isfinite(z)
            grundrate = float(y[gueltig].mean()) if gueltig.any() else np.nan
            for b in range(self.bins):
                maske = gueltig & (eimer == b)
                # Zu duenn besetzte Eimer wuerden nur Rauschen lernen.
                tabelle[b] = float(y[maske].mean()) if maske.sum() >= 100 else grundrate
            self._tables[lead] = tabelle
        return self

    def predict_rain(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        if self._edges is None or lead_hours not in self._tables:
            return np.full(len(features), np.nan)
        z = self.z_values(features)
        eimer = np.digitize(z, self._edges[1:-1])
        werte = self._tables[lead_hours][np.clip(eimer, 0, self.bins - 1)]
        return np.where(np.isnan(z), np.nan, werte)

    def predict_temp(self, features: pd.DataFrame, lead_hours: int) -> np.ndarray:
        """Zambretti sagt nichts ueber Temperatur -- bewusst nicht implementiert."""
        return np.full(len(features), np.nan)
