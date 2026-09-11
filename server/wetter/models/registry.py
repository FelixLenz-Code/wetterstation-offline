"""Modell-Register: Modelle ablegen, wiederfinden und ablösen.

Der Grund für das Register steht in der Anforderung, dass der Sensorsatz erst wächst.
Kommt ein Sensor dazu, wird nicht das laufende Modell umgebaut. Stattdessen wird ein
neues mit erweitertem Merkmalssatz trainiert, läuft erst im **Schattenbetrieb** mit
und löst das aktive erst ab, wenn die Verifikation über ein gleitendes Fenster zeigt,
dass es tatsächlich besser ist.

Das klingt umständlich, ist aber der einzige Weg, der nicht auf Vertrauen beruht: ein
zusätzlicher Sensor macht ein Modell nicht automatisch besser. Ein Sensor, der falsch
montiert oder falsch kalibriert ist, macht es messbar schlechter -- und ohne
Schattenbetrieb merkt man das erst, wenn die Vorhersage wochenlang daneben lag.
"""

from __future__ import annotations

import json
import logging
import pickle
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import lightgbm as lgb
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from wetter.db.models import Model
from wetter.models.train import RainModel, TempModel

log = logging.getLogger(__name__)

STATUS_SHADOW = "shadow"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"


class RegistryError(RuntimeError):
    """Das Modell liess sich nicht ablegen oder laden."""


@dataclass(frozen=True)
class ModelRef:
    """Zeiger auf ein abgelegtes Modell."""

    id: int
    kind: str
    target: str
    lead_hours: int
    status: str
    feature_names: list[str]
    metrics: dict


def _bundle_dir(root: Path, model_id: int) -> Path:
    return Path(root) / f"{model_id:06d}"


def _quantile_file(q: float) -> str:
    """Dateiname eines Quantils, z.B. 0.1 -> ``q010.txt``.

    Das Quantil steht im Namen, damit das Bundle auch ohne meta.json lesbar bleibt --
    hilfreich, wenn man von Hand nachsehen will, was da eigentlich liegt.
    """
    return f"q{round(float(q) * 100):03d}.txt"


def save_rain_model(
    session: Session,
    model: RainModel,
    *,
    root: Path | str,
    train_start: datetime,
    train_end: datetime,
    source: str = "dwd",
    metrics: dict | None = None,
    status: str = STATUS_SHADOW,
) -> ModelRef:
    """Legt ein Regenmodell samt Kalibrierung ab."""
    row = Model(
        kind="lightgbm_binary",
        target="rain",
        lead_hours=model.lead_hours,
        status=status,
        feature_names=list(model.feature_names),
        metrics=metrics or dict(model.metrics),
        trained_at=datetime.now(UTC),
        train_start=train_start,
        train_end=train_end,
        source=source,
    )
    session.add(row)
    session.flush()

    ziel = _bundle_dir(root, row.id)
    ziel.mkdir(parents=True, exist_ok=True)
    model.booster.save_model(str(ziel / "booster.txt"))
    if model.calibrator is not None:
        # Isotonic kennt kein eigenes Textformat -- pickle ist hier vertretbar, weil
        # die Datei nie das eigene Dateisystem verlaesst.
        with open(ziel / "calibrator.pkl", "wb") as fh:
            pickle.dump(model.calibrator, fh)
    (ziel / "meta.json").write_text(
        json.dumps(
            {
                "target": "rain",
                "lead_hours": model.lead_hours,
                "feature_names": list(model.feature_names),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    row.artifact_path = str(ziel)
    session.flush()
    log.info("Regenmodell %d abgelegt (%d h, %s)", row.id, model.lead_hours, status)
    return _to_ref(row)


def save_temp_model(
    session: Session,
    model: TempModel,
    *,
    root: Path | str,
    train_start: datetime,
    train_end: datetime,
    source: str = "dwd",
    metrics: dict | None = None,
    status: str = STATUS_SHADOW,
) -> ModelRef:
    """Legt die Quantilmodelle einer Vorlaufzeit ab."""
    row = Model(
        kind="lightgbm_quantile",
        target="temperature",
        lead_hours=model.lead_hours,
        status=status,
        feature_names=list(model.feature_names),
        metrics=metrics or dict(model.metrics),
        trained_at=datetime.now(UTC),
        train_start=train_start,
        train_end=train_end,
        source=source,
    )
    session.add(row)
    session.flush()

    ziel = _bundle_dir(root, row.id)
    ziel.mkdir(parents=True, exist_ok=True)
    for q, booster in model.boosters.items():
        booster.save_model(str(ziel / _quantile_file(q)))
    (ziel / "meta.json").write_text(
        json.dumps(
            {
                "target": "temperature",
                "lead_hours": model.lead_hours,
                "quantiles": sorted(model.boosters),
                "feature_names": list(model.feature_names),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    row.artifact_path = str(ziel)
    session.flush()
    log.info("Temperaturmodell %d abgelegt (%d h, %s)", row.id, model.lead_hours, status)
    return _to_ref(row)


#: Geladene Modelle, nach Kennung. Ein Modell ist unveränderlich: ein erneutes
#: Training legt eine neue Zeile mit neuer Kennung an, statt eine bestehende zu
#: überschreiben. Deshalb ist Zwischenspeichern hier gefahrlos.
#:
#: Ohne den Zwischenspeicher lädt jede Vorhersage alle Modelle neu von der Platte.
#: Im Zehn-Minuten-Takt wäre das verschmerzbar, beim Nachrechnen eines längeren
#: Zeitraums sind es Tausende Ladevorgänge und der Lauf dauert ein Vielfaches.
_CACHE: dict[int, RainModel | TempModel] = {}

#: Mehr als so viele Modelle werden nicht vorgehalten. Ein Bündel liegt im
#: einstelligen Megabyte-Bereich; der Deckel verhindert, dass ein lang laufender
#: Worker über Monate jede je trainierte Fassung im Speicher behält.
CACHE_LIMIT = 32


def clear_cache() -> None:
    """Leert den Modell-Zwischenspeicher. Für Tests und nach einem Re-Training."""
    _CACHE.clear()


def _cache_put(model_id: int, model: RainModel | TempModel) -> None:
    if len(_CACHE) >= CACHE_LIMIT:
        # Ältesten Eintrag verwerfen -- dict behält die Einfügereihenfolge.
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[model_id] = model


def load_rain_model(session: Session, model_id: int) -> RainModel:
    zwischengespeichert = _CACHE.get(model_id)
    if isinstance(zwischengespeichert, RainModel):
        return zwischengespeichert

    row = session.get(Model, model_id)
    if row is None or row.artifact_path is None:
        raise RegistryError(f"Modell {model_id} nicht gefunden")
    ziel = Path(row.artifact_path)
    booster = lgb.Booster(model_file=str(ziel / "booster.txt"))
    kalibrator = None
    pfad = ziel / "calibrator.pkl"
    if pfad.is_file():
        with open(pfad, "rb") as fh:
            kalibrator = pickle.load(fh)
    modell = RainModel(
        lead_hours=row.lead_hours,
        booster=booster,
        calibrator=kalibrator,
        feature_names=list(row.feature_names),
        metrics=dict(row.metrics or {}),
    )
    _cache_put(model_id, modell)
    return modell


def load_temp_model(session: Session, model_id: int) -> TempModel:
    zwischengespeichert = _CACHE.get(model_id)
    if isinstance(zwischengespeichert, TempModel):
        return zwischengespeichert

    row = session.get(Model, model_id)
    if row is None or row.artifact_path is None:
        raise RegistryError(f"Modell {model_id} nicht gefunden")
    ziel = Path(row.artifact_path)
    meta = json.loads((ziel / "meta.json").read_text(encoding="utf-8"))
    boosters = {
        float(q): lgb.Booster(model_file=str(ziel / _quantile_file(q)))
        for q in meta["quantiles"]
    }
    modell = TempModel(
        lead_hours=row.lead_hours,
        boosters=boosters,
        feature_names=list(row.feature_names),
        metrics=dict(row.metrics or {}),
    )
    _cache_put(model_id, modell)
    return modell


def _to_ref(row: Model) -> ModelRef:
    return ModelRef(
        id=row.id,
        kind=row.kind,
        target=row.target,
        lead_hours=row.lead_hours,
        status=row.status,
        feature_names=list(row.feature_names or []),
        metrics=dict(row.metrics or {}),
    )


def list_models(
    session: Session,
    *,
    target: str | None = None,
    status: str | None = None,
) -> list[ModelRef]:
    stmt = select(Model)
    if target is not None:
        stmt = stmt.where(Model.target == target)
    if status is not None:
        stmt = stmt.where(Model.status == status)
    stmt = stmt.order_by(Model.target, Model.lead_hours, Model.trained_at.desc())
    return [_to_ref(r) for r in session.scalars(stmt).all()]


def active_model(session: Session, target: str, lead_hours: int) -> ModelRef | None:
    """Das derzeit aktive Modell für ein Ziel und eine Vorlaufzeit."""
    row = session.scalar(
        select(Model).where(
            Model.target == target,
            Model.lead_hours == lead_hours,
            Model.status == STATUS_ACTIVE,
        )
    )
    return _to_ref(row) if row else None


def promote(session: Session, model_id: int) -> ModelRef:
    """Macht ein Modell aktiv und versetzt das bisherige in den Ruhestand.

    Bewusst ohne Prüfung der Güte -- die Entscheidung trifft
    :func:`wetter.worker.promotion.evaluate_shadow`, die dafür die Verifikation
    heranzieht. Hier steht nur der Buchungsvorgang.
    """
    row = session.get(Model, model_id)
    if row is None:
        raise RegistryError(f"Modell {model_id} nicht gefunden")

    session.execute(
        update(Model)
        .where(
            Model.target == row.target,
            Model.lead_hours == row.lead_hours,
            Model.status == STATUS_ACTIVE,
            Model.id != model_id,
        )
        .values(status=STATUS_RETIRED)
    )
    row.status = STATUS_ACTIVE
    session.flush()
    log.info(
        "Modell %d ist jetzt aktiv (%s, %d h)", row.id, row.target, row.lead_hours
    )
    return _to_ref(row)


def missing_features(ref: ModelRef, available: list[str]) -> list[str]:
    """Merkmale, die das Modell erwartet, die aber gerade nicht geliefert werden.

    Kein Fehler, sondern eine Information: LightGBM kommt mit fehlenden Werten
    zurecht. Aber es gehört ins Log und in die Oberfläche, denn ein Modell, dem die
    Hälfte seiner Merkmale fehlt, sagt nicht mehr das voraus, wofür es trainiert
    wurde -- auch wenn es klaglos eine Zahl liefert.
    """
    vorhanden = set(available)
    return [f for f in ref.feature_names if f not in vorhanden]
