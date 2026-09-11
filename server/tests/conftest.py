"""Gemeinsame Fixtures.

Die Datenbanktests laufen gegen ein echtes Postgres statt gegen SQLite. Der Grund
ist nicht Prinzipienreiterei: der Ingest lebt von ``INSERT ... ON CONFLICT DO UPDATE``
mit ``coalesce`` auf ``excluded``, von JSONB und von BRIN-Indizes. Nichts davon gibt
es in SQLite -- ein Test dagegen würde grüne Haken liefern und im Betrieb scheitern.

Ohne erreichbare Datenbank werden die Tests übersprungen statt rot. Die reinen
Rechenteile -- Meteorologie, Merkmale, Modelle -- brauchen keine Datenbank und laufen
immer.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from wetter.db.models import SCHEMA, Base

TEST_URL_ENV = "WETTER_TEST_DATABASE_URL"

#: Die Sitzungs-Fixture leert vor jedem Test alle Tabellen. Zeigt sie versehentlich
#: auf eine Datenbank mit echten Daten, ist die Messreihe weg -- und zwar ohne
#: Rückfrage und ohne dass irgendetwas nach einem Fehler aussieht.
#:
#: Das ist keine theoretische Sorge: beim Entwickeln hat genau das einmal einem
#: laufenden Experiment die 272.000 importierten DWD-Stunden unter den Füßen
#: weggezogen. Deshalb muss der Datenbankname erkennbar eine Testdatenbank
#: bezeichnen.
TEST_DB_MARKER = ("test", "ci", "tmp")


def _ist_testdatenbank(url: str) -> bool:
    name = url.rsplit("/", 1)[-1].split("?")[0].lower()
    return any(m in name for m in TEST_DB_MARKER)


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get(TEST_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_URL_ENV} nicht gesetzt -- Datenbanktests uebersprungen")
    if not _ist_testdatenbank(url):
        pytest.fail(
            f"{TEST_URL_ENV} zeigt auf die Datenbank "
            f"{url.rsplit('/', 1)[-1]!r}. Die Tests leeren vor jedem Lauf alle "
            f"Tabellen -- der Name muss deshalb erkennbar eine Testdatenbank "
            f"bezeichnen (einer von {', '.join(TEST_DB_MARKER)})."
        )

    eng = create_engine(url, future=True)
    try:
        with eng.connect() as con:
            con.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
            con.commit()
    except SQLAlchemyError as exc:
        pytest.skip(f"Datenbank nicht erreichbar: {exc}")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Session:
    """Frische, leere Tabellen je Test.

    TRUNCATE statt Rollback: der Ingest ruft selbst ``flush`` und legt Stationen an,
    eine geschachtelte Transaktion würde das Verhalten verfälschen.
    """
    tabellen = ", ".join(
        f'"{SCHEMA}"."{t.name}"' for t in reversed(Base.metadata.sorted_tables)
    )
    with engine.connect() as con:
        con.execute(text(f"TRUNCATE {tabellen} RESTART IDENTITY CASCADE"))
        con.commit()

    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        yield s
