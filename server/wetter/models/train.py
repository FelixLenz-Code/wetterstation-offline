"""Training der Vorhersagemodelle.

LightGBM ist hier nicht aus Gewohnheit gewaehlt, sondern wegen einer Eigenschaft, die
fuer dieses Projekt entscheidend ist: **fehlende Werte werden nativ behandelt**. Der
Sensorsatz waechst erst noch, einzelne Sensoren sind abschaltbar und stehen zeitweise
im Testmodus. Ein Verfahren, das vorher eine Luecke gefuellt haben will, muesste raten
-- und wuerde beim Ausfall eines Sensors still schlechter werden, ohne dass es jemand
merkt.

Zwei Zielarten, zwei Verfahren:

* **Regen** -- Klassifikation je Vorlaufzeit, danach Isotonic-Kalibrierung. Ohne die
  Kalibrierung sind die ausgegebenen Prozentzahlen reine Zierde: ein Baummodell
  optimiert auf Trennschaerfe, nicht darauf, dass "70 %" auch 70 % bedeutet.
* **Temperatur** -- Quantilregression auf 0,1 / 0,5 / 0,9. Ergibt statt einer Zahl
  ein Band, und das Band ist bei einer Punktmessung die ehrlichere Aussage.

Getrennte Modelle je Vorlaufzeit (statt eines rekursiven Modells, das sich selbst
fuettert): Fehler schaukeln sich sonst mit jeder Iteration auf.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from wetter.models.split import TimeSplit

log = logging.getLogger(__name__)

#: Quantile der Temperaturvorhersage: unteres Band, Median, oberes Band.
TEMP_QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)

#: Voreinstellungen, bewusst konservativ -- bei 45 Merkmalen und einer stark
#: autokorrelierten Zeitreihe ueberanpasst ein tiefes Modell schnell.
LGB_DEFAULTS: dict[str, Any] = {
    "num_leaves": 63,
    "learning_rate": 0.05,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "verbose": -1,
    "num_threads": 0,
}


@dataclass
class RainModel:
    """Ein kalibriertes Regenmodell fuer genau eine Vorlaufzeit."""

    lead_hours: int
    booster: lgb.Booster
    calibrator: IsotonicRegression | None
    feature_names: list[str]
    metrics: dict[str, float] = field(default_factory=dict)

    def predict(self, features: pd.DataFrame) -> np.ndarray:
        roh = self.booster.predict(features[self.feature_names])
        roh = np.asarray(roh, dtype=float)
        if self.calibrator is None:
            return roh
        return self.calibrator.predict(roh)


@dataclass
class TempModel:
    """Quantilmodelle fuer eine Vorlaufzeit."""

    lead_hours: int
    boosters: dict[float, lgb.Booster]
    feature_names: list[str]
    metrics: dict[str, float] = field(default_factory=dict)
    conformal_width: float = 0.0
    """Aufweitung des Bandes in Kelvin, damit es wirklich so oft trifft wie
    versprochen. Siehe :func:`conformal_width`."""

    def predict(self, features: pd.DataFrame) -> dict[float, np.ndarray]:
        x = features[self.feature_names]
        roh = {
            q: np.asarray(b.predict(x), dtype=float) for q, b in self.boosters.items()
        }
        sortiert = _sort_quantiles(roh)
        if self.conformal_width <= 0.0:
            return sortiert

        # Nur die Bandgrenzen aufweiten, den Median nicht verschieben: er ist die
        # beste Punktschaetzung und wird durch die Aufweitung nicht besser.
        qs = sorted(sortiert)
        unten, oben = qs[0], qs[-1]
        out = dict(sortiert)
        out[unten] = sortiert[unten] - self.conformal_width
        out[oben] = sortiert[oben] + self.conformal_width
        return out


def _sort_quantiles(werte: dict[float, np.ndarray]) -> dict[float, np.ndarray]:
    """Erzwingt, dass die Quantile aufsteigend sind.

    Getrennt trainierte Quantilmodelle koennen sich ueberkreuzen -- das untere Band
    laege dann ueber dem oberen. Sortieren ist die einfachste Abhilfe und aendert an
    der Guete praktisch nichts.
    """
    qs = sorted(werte)
    gestapelt = np.sort(np.vstack([werte[q] for q in qs]), axis=0)
    return {q: gestapelt[i] for i, q in enumerate(qs)}


def _clean(x: pd.DataFrame, y: pd.Series) -> tuple[pd.DataFrame, np.ndarray]:
    """Entfernt Zeilen ohne Zielwert. Fehlende *Merkmale* bleiben drin."""
    gueltig = y.notna().to_numpy()
    return x.loc[gueltig], y.to_numpy(dtype=float)[gueltig]


def train_rain_model(
    features: pd.DataFrame,
    target: pd.Series,
    *,
    lead_hours: int,
    split: TimeSplit,
    num_boost_round: int = 400,
    early_stopping_rounds: int = 40,
    params: dict[str, Any] | None = None,
) -> RainModel:
    """Trainiert und kalibriert ein Regenmodell."""
    tr, ca, _ = split.masks(features.index)
    namen = list(features.columns)

    x_tr, y_tr = _clean(features.loc[tr.to_numpy()], target.loc[tr.to_numpy()])
    x_ca, y_ca = _clean(features.loc[ca.to_numpy()], target.loc[ca.to_numpy()])
    if not len(x_tr) or not len(x_ca):
        raise ValueError(f"zu wenige Daten fuer rain_next_{lead_hours}h")

    p = {**LGB_DEFAULTS, "objective": "binary", "metric": "binary_logloss"}
    p.update(params or {})

    booster = lgb.train(
        p,
        lgb.Dataset(x_tr, label=y_tr, feature_name=namen),
        num_boost_round=num_boost_round,
        valid_sets=[lgb.Dataset(x_ca, label=y_ca, feature_name=namen)],
        callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
    )

    # Kalibrierung auf demselben Abschnitt, der das fruehe Stoppen gesteuert hat.
    # Das ist vertretbar, weil Isotonic nur eine monotone Umskalierung lernt und der
    # Testabschnitt davon unberuehrt bleibt.
    roh_ca = np.asarray(booster.predict(x_ca), dtype=float)
    kalibrator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    kalibrator.fit(roh_ca, y_ca)

    return RainModel(
        lead_hours=lead_hours,
        booster=booster,
        calibrator=kalibrator,
        feature_names=namen,
        metrics={"best_iteration": float(booster.best_iteration or num_boost_round)},
    )


def conformal_width(
    lower: np.ndarray,
    upper: np.ndarray,
    observed: np.ndarray,
    *,
    coverage: float = 0.8,
) -> float:
    """Berechnet, um wieviel das Band aufgeweitet werden muss.

    Quantilregression liefert Baender, die auf den Trainingsdaten passen und auf
    neuen Daten regelmaessig zu eng sind -- das Modell ist selbstbewusster, als es
    sein darf. Gemessen an einem vollen Durchlauf deckte ein 10-bis-90-Prozent-Band
    nur 51 statt 80 Prozent der Faelle ab. Ein Band, das seine eigene Zusage um 30
    Punkte verfehlt, ist schlimmer als gar keines: es sieht nach Wissen aus.

    Das Verfahren ist die konformalisierte Quantilregression nach Romano, Patterson
    und Candes (2019). Auf einem Abschnitt, den die Modelle nicht zum Lernen gesehen
    haben, wird je Fall gemessen, wie weit die Beobachtung aus dem Band herausragt:

        E = max(untere Grenze - Beobachtung, Beobachtung - obere Grenze)

    Negative Werte heissen, die Beobachtung lag bequem innerhalb. Das passende
    empirische Quantil dieser Abstaende ist die gesuchte Aufweitung. Die daraus
    folgende Abdeckung gilt garantiert und ohne Annahme ueber die Verteilung --
    vorausgesetzt, der Kalibrierabschnitt aehnelt dem spaeteren Betrieb.

    Ein negatives Ergebnis wird auf null gesetzt: ein zu weites Band wieder
    einzuengen waere zwar erlaubt, aber die Abdeckung ist die Zusage, die zaehlt.
    """
    gueltig = np.isfinite(lower) & np.isfinite(upper) & np.isfinite(observed)
    if gueltig.sum() < 100:
        return 0.0

    lo, hi, y = lower[gueltig], upper[gueltig], observed[gueltig]
    abstand = np.maximum(lo - y, y - hi)

    n = len(abstand)
    # Die Korrektur um (n+1)/n ist der Kern der endlichen Garantie: sie sorgt
    # dafuer, dass die Zusage auch bei kleinem Kalibrierabschnitt haelt.
    rang = min(1.0, np.ceil((n + 1) * coverage) / n)
    return float(max(0.0, np.quantile(abstand, rang)))


def train_temp_model(
    features: pd.DataFrame,
    target: pd.Series,
    *,
    lead_hours: int,
    split: TimeSplit,
    quantiles: tuple[float, ...] = TEMP_QUANTILES,
    num_boost_round: int = 400,
    early_stopping_rounds: int = 40,
    params: dict[str, Any] | None = None,
) -> TempModel:
    """Trainiert die Quantilmodelle einer Vorlaufzeit."""
    tr, ca, _ = split.masks(features.index)
    namen = list(features.columns)

    x_tr, y_tr = _clean(features.loc[tr.to_numpy()], target.loc[tr.to_numpy()])
    x_ca, y_ca = _clean(features.loc[ca.to_numpy()], target.loc[ca.to_numpy()])
    if not len(x_tr) or not len(x_ca):
        raise ValueError(f"zu wenige Daten fuer temp_at_{lead_hours}h")

    # Der Kalibrierabschnitt wird geteilt: die erste Haelfte steuert das fruehe
    # Stoppen, die zweite bleibt fuer die konformale Aufweitung unberuehrt. Beides
    # auf denselben Daten zu machen hiesse, die Aufweitung auf Daten zu messen, an
    # denen die Modelle bereits ausgerichtet wurden -- das Band fiele wieder zu eng
    # aus, also genau der Fehler, den die Aufweitung beheben soll.
    mitte = len(x_ca) // 2
    x_stop, y_stop = x_ca.iloc[:mitte], y_ca[:mitte]
    x_konf, y_konf = x_ca.iloc[mitte:], y_ca[mitte:]

    boosters: dict[float, lgb.Booster] = {}
    for q in quantiles:
        p = {**LGB_DEFAULTS, "objective": "quantile", "alpha": q, "metric": "quantile"}
        p.update(params or {})
        boosters[q] = lgb.train(
            p,
            lgb.Dataset(x_tr, label=y_tr, feature_name=namen),
            num_boost_round=num_boost_round,
            valid_sets=[lgb.Dataset(x_stop, label=y_stop, feature_name=namen)],
            callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
        )

    modell = TempModel(lead_hours=lead_hours, boosters=boosters, feature_names=namen)

    qs = sorted(quantiles)
    abdeckung = qs[-1] - qs[0]
    roh = modell.predict(x_konf)
    breite = conformal_width(roh[qs[0]], roh[qs[-1]], y_konf, coverage=abdeckung)
    modell.conformal_width = breite
    modell.metrics = {
        "conformal_width": breite,
        "conformal_samples": len(x_konf),
        "nominal_coverage": abdeckung,
    }
    if breite > 0:
        log.info(
            "Temperaturmodell %d h: Band um %.2f K aufgeweitet (%d Faelle)",
            lead_hours,
            breite,
            len(x_konf),
        )
    return modell


def feature_importance(booster: lgb.Booster, *, top: int = 15) -> list[tuple[str, float]]:
    """Wichtigste Merkmale nach Informationsgewinn.

    Nicht fuer die Vorhersage noetig, aber fuer das Vertrauen in sie: wenn ganz oben
    nicht die Drucktendenz steht, stimmt etwas nicht.
    """
    gewinn = booster.feature_importance(importance_type="gain")
    paare = sorted(
        zip(booster.feature_name(), gewinn, strict=True),
        key=lambda kv: kv[1],
        reverse=True,
    )
    gesamt = sum(gewinn) or 1.0
    return [(n, float(g) / gesamt) for n, g in paare[:top]]
