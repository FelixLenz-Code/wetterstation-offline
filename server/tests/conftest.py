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


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get(TEST_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_URL_ENV} nicht gesetzt -- Datenbanktests uebersprungen")
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
