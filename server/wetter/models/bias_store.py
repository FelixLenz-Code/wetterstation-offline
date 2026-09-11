"""Bias-Abbildungen ablegen und wieder laden.

Ohne diesen Schritt ist die Bias-Korrektur wirkungslos, und zwar auf die
unangenehmste Art: sie *scheint* zu wirken, weil das Training sie benutzt, aber die
Vorhersage läuft danach auf unkorrigierten Werten. Das Modell denkt in DWD-Werten
und bekommt die Rohwerte der eigenen Station -- ein Versatz zwischen Training und
Betrieb, der nichts kaputtmacht, was nach einem Fehler aussieht, sondern einfach die
Güte frisst.

Gemessen an einem vollen Durchlauf mit 0,8 K Aufstellungsversatz: der mittlere
Temperaturfehler auf sechs Stunden stieg von 1,54 auf 2,32 K, und das 10-bis-90-
Prozent-Band deckte statt der erwarteten 80 nur noch 50 Prozent der Fälle ab.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wetter.db.models import BiasMapping, Station
from wetter.models.bias import QuantileMapping, apply_all

log = logging.getLogger(__name__)


def save_mappings(
    session: Session, station: Station, mappings: dict[str, QuantileMapping]
) -> int:
    """Legt die gelernten Abbildungen ab und entfernt veraltete.

    Entfernt wird bewusst: fällt eine Spalte aus der Anpassung heraus -- etwa weil
    der Sensor abgeklemmt wurde und keine gemeinsamen Stunden mehr zustande kommen --
    darf keine alte Abbildung stehenbleiben und weiter angewendet werden.
    """
    vorhanden = set(
        session.scalars(
            select(BiasMapping.column_name).where(
                BiasMapping.station_id == station.id
            )
        ).all()
    )
    veraltet = vorhanden - set(mappings)
    if veraltet:
        session.execute(
            delete(BiasMapping).where(
                BiasMapping.station_id == station.id,
                BiasMapping.column_name.in_(veraltet),
            )
        )
        log.info("Bias-Korrektur für %s entfernt", ", ".join(sorted(veraltet)))

    if not mappings:
        return 0

    jetzt = datetime.now(UTC)
    zeilen = [
        {
            "station_id": station.id,
            "column_name": spalte,
            "payload": abbildung.to_dict(),
            "samples": abbildung.samples,
            "median_shift": abbildung.median_shift,
            "fitted_at": jetzt,
        }
        for spalte, abbildung in mappings.items()
    ]
    stmt = insert(BiasMapping).values(zeilen)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=[BiasMapping.station_id, BiasMapping.column_name],
            set_={
                "payload": stmt.excluded.payload,
                "samples": stmt.excluded.samples,
                "median_shift": stmt.excluded.median_shift,
                "fitted_at": stmt.excluded.fitted_at,
            },
        )
    )
    return len(zeilen)


def load_mappings(session: Session, station: Station) -> dict[str, QuantileMapping]:
    """Lädt die abgelegten Abbildungen einer Station."""
    zeilen = session.scalars(
        select(BiasMapping).where(BiasMapping.station_id == station.id)
    ).all()
    return {z.column_name: QuantileMapping.from_dict(z.payload) for z in zeilen}


def apply_to_frame(
    session: Session, station: Station, frame: pd.DataFrame
) -> tuple[pd.DataFrame, list[str]]:
    """Bringt eigene Messwerte auf die DWD-Skala, in der die Modelle denken.

    Gibt zusätzlich zurück, welche Spalten korrigiert wurden -- das gehört in die
    Oberfläche, damit sichtbar ist, dass die angezeigten Messwerte roh sind, die
    Vorhersage aber auf korrigierten beruht.
    """
    abbildungen = load_mappings(session, station)
    if not abbildungen:
        return frame, []
    genutzt = [c for c in abbildungen if c in frame.columns]
    return apply_all(frame, abbildungen), sorted(genutzt)
