"""Abgelaufene Vorhersagen gegen die Messung bewerten.

Der Teil, der die Vorhersage von einer Behauptung trennt. Erst hier entsteht die
Zahl, die in der Oberfläche stehen soll: nicht "70 Prozent Regen", sondern "wenn
diese Station 70 Prozent sagt, regnet es in 68 Prozent der Fälle".

Eine Feinheit, die leicht falsch läuft: die Beobachtung zu einer Regenvorhersage ist
nicht der Zustand zum Zeitpunkt ``valid_at``, sondern ob es **irgendwann im Fenster**
zwischen Ausstellung und Gültigkeit geregnet hat. Wer nur die Zielstunde nachschlägt,
bewertet etwas anderes, als das Modell vorhergesagt hat -- und bekommt eine viel zu
schlechte Trefferquote.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wetter.db.models import Forecast, Hourly, Verification
from wetter.features.targets import RAIN_THRESHOLD_MM

log = logging.getLogger(__name__)


def observed_rain(
    session: Session, station_id: int, start: datetime, end: datetime
) -> float | None:
    """Hat es im Fenster ``(start, end]`` geregnet? 1.0, 0.0 oder ``None``.

    ``None``, wenn das Fenster Lücken hat. Eine Lücke als "kein Regen" zu werten wäre
    der klassische Weg, sich eine zu gute Trefferquote anzurechnen: ausgerechnet bei
    Starkregen fällt eine Station gern aus.
    """
    zeile = session.execute(
        select(
            func.count(Hourly.time),
            func.count(Hourly.precip_mm),
            func.max(Hourly.precip_mm),
        ).where(
            Hourly.station_id == station_id,
            Hourly.time > start,
            Hourly.time <= end,
        )
    ).one()
    stunden, mit_wert, groesste = zeile

    erwartet = max(1, int((end - start).total_seconds() // 3600))
    if stunden < erwartet or mit_wert < erwartet:
        return None
    return 1.0 if (groesste or 0.0) >= RAIN_THRESHOLD_MM else 0.0


def observed_temperature(
    session: Session, station_id: int, valid_at: datetime
) -> float | None:
    """Die gemessene Temperatur zur Gültigkeitsstunde."""
    return session.scalar(
        select(Hourly.temperature_c).where(
            Hourly.station_id == station_id,
            Hourly.time == valid_at,
        )
    )


def score_forecast(
    target: str,
    value: float | None,
    quantiles: dict | None,
    observed: float,
) -> dict:
    """Rechnet die zum Ziel passenden Gütemasse.

    Für Regen der Brier Score, für Temperatur der absolute Fehler plus die
    Pinball-Verluste der Quantile. Aus letzteren entsteht später der CRPS, der
    bewertet, ob auch das *Band* stimmt und nicht nur die Mitte.
    """
    out: dict[str, float] = {"observed": observed}
    if value is None:
        return out

    if target == "rain":
        out["brier"] = float((value - observed) ** 2)
        out["hit"] = float((value >= 0.5) == (observed >= 0.5))
        return out

    if target == "temperature":
        out["absolute_error"] = float(abs(value - observed))
        out["squared_error"] = float((value - observed) ** 2)
        for q_str, q_wert in (quantiles or {}).items():
            q = float(q_str)
            diff = observed - float(q_wert)
            out[f"pinball_{q_str}"] = float(max(q * diff, (q - 1.0) * diff))
        if quantiles and "0.1" in quantiles and "0.9" in quantiles:
            unten, oben = float(quantiles["0.1"]), float(quantiles["0.9"])
            out["in_band"] = float(unten <= observed <= oben)
    return out


def verify_pending(
    session: Session, *, now: datetime | None = None, limit: int = 5000
) -> int:
    """Bewertet alle abgelaufenen, noch nicht bewerteten Vorhersagen."""
    jetzt = now or datetime.now(UTC)

    offen = session.scalars(
        select(Forecast)
        .outerjoin(Verification, Verification.forecast_id == Forecast.id)
        .where(Forecast.valid_at <= jetzt, Verification.forecast_id.is_(None))
        .order_by(Forecast.valid_at)
        .limit(limit)
    ).all()
    if not offen:
        return 0

    zeilen = []
    ohne_messung = 0
    for f in offen:
        if f.target == "rain":
            beobachtet = observed_rain(session, f.station_id, f.issued_at, f.valid_at)
        elif f.target == "temperature":
            beobachtet = observed_temperature(session, f.station_id, f.valid_at)
        else:
            continue

        if beobachtet is None:
            # Noch nicht bewertbar: die Messung fehlt (noch). Der nächste Lauf
            # versucht es erneut -- Nachzügler aus dem Ringpuffer füllen Lücken auf.
            ohne_messung += 1
            continue

        werte = score_forecast(f.target, f.value, f.quantiles, float(beobachtet))
        zeilen.append(
            {
                "forecast_id": f.id,
                "verified_at": jetzt,
                "observed": float(beobachtet),
                "scores": werte,
            }
        )

    if zeilen:
        stmt = insert(Verification).values(zeilen)
        session.execute(
            stmt.on_conflict_do_nothing(index_elements=[Verification.forecast_id])
        )

    log.info(
        "%d Vorhersagen bewertet, %d warten noch auf die Messung",
        len(zeilen),
        ohne_messung,
    )
    return len(zeilen)
