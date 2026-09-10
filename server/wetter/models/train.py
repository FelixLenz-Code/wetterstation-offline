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

    def predict(self, features: pd.DataFrame) -> dict[float, np.ndarray]:
        x = features[self.feature_names]
        roh = {q: np.asarray(b.predict(x), dtype=float) for q, b in self.boosters.items()}
        return _sort_quantiles(roh)


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

    boosters: dict[float, lgb.Booster] = {}
    for q in quantiles:
        p = {**LGB_DEFAULTS, "objective": "quantile", "alpha": q, "metric": "quantile"}
        p.update(params or {})
        boosters[q] = lgb.train(
            p,
            lgb.Dataset(x_tr, label=y_tr, feature_name=namen),
            num_boost_round=num_boost_round,
            valid_sets=[lgb.Dataset(x_ca, label=y_ca, feature_name=namen)],
            callbacks=[lgb.early_stopping(early_stopping_rounds, verbose=False)],
        )

    return TempModel(lead_hours=lead_hours, boosters=boosters, feature_names=namen)


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
