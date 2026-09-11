"""Datenbankverbindung.

Die Zugangsdaten kommen ausschließlich aus der Umgebung -- nichts davon gehört ins
Repository, und der Docker-Compose-Stack reicht sie ohnehin so durch.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


class DbSettings(BaseSettings):
    """Verbindungsdaten aus der Umgebung."""

    model_config = SettingsConfigDict(env_prefix="WETTER_", extra="ignore")

    database_url: str = "postgresql+psycopg://wetterapp:wetter@localhost:5432/wetter"
    echo_sql: bool = False


def make_engine(url: str | None = None, *, echo: bool | None = None) -> Engine:
    settings = DbSettings()
    return create_engine(
        url or settings.database_url,
        echo=settings.echo_sql if echo is None else echo,
        # Der Worker liegt zwischen den Läufen lange still; ohne pre_ping bekommt er
        # nach einer Server-Wartung eine tote Verbindung in die Hand.
        pool_pre_ping=True,
        future=True,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Sitzung mit Commit am Ende und Rollback im Fehlerfall."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
