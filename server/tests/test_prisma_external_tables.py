"""Prüft, dass Prisma alle Alembic-Tabellen als fremdverwaltet kennt.

Entstanden aus einem Beinahe-Unfall: nach dem Hinzufügen der Tabelle
``bias_mapping`` bot ``prisma migrate dev`` an, *beide* Schemata zurückzusetzen --
also die gesamte Messreihe zu löschen. Grund war, dass die neue Tabelle nicht in
der Liste ``tables.external`` in ``web/prisma.config.ts`` stand und Prisma sie
deshalb für eine Abweichung von seinem eigenen Schema hielt.

Dieser Test vergleicht die Liste mit den SQLAlchemy-Modellen und schlägt fehl,
sobald jemand eine Tabelle hinzufügt und den Eintrag vergisst -- also in der CI
statt beim nächsten Migrationslauf auf dem Rechner mit den echten Daten.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from wetter.db.models import SCHEMA, Base

CONFIG = (
    pathlib.Path(__file__).resolve().parents[2] / "web" / "prisma.config.ts"
)


def gelistete_tabellen() -> set[str]:
    text = CONFIG.read_text(encoding="utf-8")
    block = re.search(r"external:\s*\[(.*?)\]", text, re.S)
    if block is None:
        return set()
    return set(re.findall(r'"([^"]+)"', block.group(1)))


@pytest.mark.skipif(not CONFIG.is_file(), reason="web/prisma.config.ts nicht vorhanden")
def test_alle_alembic_tabellen_sind_als_fremd_eingetragen():
    gelistet = gelistete_tabellen()
    erwartet = {f"{SCHEMA}.{t.name}" for t in Base.metadata.sorted_tables}
    fehlend = sorted(erwartet - gelistet)

    assert not fehlend, (
        "Diese Tabellen fehlen in tables.external in web/prisma.config.ts:\n  "
        + "\n  ".join(fehlend)
        + "\n\nOhne den Eintrag bietet `prisma migrate dev` an, das Schema "
        f"{SCHEMA!r} zurückzusetzen -- also die Messreihe zu löschen."
    )


@pytest.mark.skipif(not CONFIG.is_file(), reason="web/prisma.config.ts nicht vorhanden")
def test_verwaltungstabelle_von_alembic_ist_eingetragen():
    """Auch alembic_version gehört in die Liste, sonst will Prisma sie anfassen."""
    assert f"{SCHEMA}.alembic_version" in gelistete_tabellen()


@pytest.mark.skipif(not CONFIG.is_file(), reason="web/prisma.config.ts nicht vorhanden")
def test_liste_enthaelt_nichts_erfundenes():
    """Ein Eintrag ohne passende Tabelle ist meist ein Tippfehler."""
    bekannt = {f"{SCHEMA}.{t.name}" for t in Base.metadata.sorted_tables}
    bekannt.add(f"{SCHEMA}.alembic_version")
    ueberfluessig = sorted(gelistete_tabellen() - bekannt)
    assert not ueberfluessig, f"Unbekannte Einträge: {ueberfluessig}"


@pytest.mark.skipif(not CONFIG.is_file(), reason="web/prisma.config.ts nicht vorhanden")
def test_experimentalflag_ist_gesetzt():
    """Ohne experimental.externalTables ignoriert Prisma die ganze Liste."""
    text = CONFIG.read_text(encoding="utf-8")
    assert "externalTables: true" in text
