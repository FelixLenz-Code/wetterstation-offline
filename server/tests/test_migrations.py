"""Prüft, dass Migrationen und Modelle nicht auseinanderlaufen.

Entstanden aus einem Fehler, der lange gesucht war: Alembic schrieb bei *jedem*
Autogenerate dieselben sechs Fremdschlüssel als entfernt und wieder hinzugefügt in
eine neue Migration -- mit ``drop_constraint``-Aufrufen ohne Schemaangabe, die zur
Laufzeit gescheitert wären.

Die Ursache war, dass der Datenbankbenutzer genauso hiess wie das Schema. Postgres
hat den Standard-Suchpfad ``"$user", public``; heisst der Benutzer ``wetter`` und
das Schema auch, ist ``wetter`` das Standardschema der Verbindung. SQLAlchemy
normalisiert dann bei der Reflexion ``wetter.forecast`` zu ``forecast`` ohne Schema,
während die Metadaten ``wetter.forecast`` sagen -- und Alembic hält beides für
verschiedene Tabellen.

Deshalb heisst der Benutzer ``wetterapp``. Der Test hier fängt einen Rückfall ab,
und ganz nebenbei auch den häufigeren Fall, dass jemand ein Modell ändert und die
Migration vergisst.
"""

from __future__ import annotations

import pytest
from alembic.autogenerate import produce_migrations
from alembic.migration import MigrationContext
from sqlalchemy import text

from wetter.db.models import SCHEMA, Base

VERWALTUNGSTABELLEN = {"alembic_version", "_prisma_migrations"}


def _diffs(connection) -> list:
    """Unterschiede zwischen Modellen und tatsächlichem Datenbankschema."""
    ctx = MigrationContext.configure(
        connection,
        opts={
            "include_schemas": True,
            "include_name": lambda name, typ, eltern: (
                name == SCHEMA if typ == "schema" else True
            ),
            "include_object": lambda o, name, typ, refl, cmp: (
                name not in VERWALTUNGSTABELLEN
            ),
            "version_table_schema": SCHEMA,
            "compare_type": True,
        },
    )
    return produce_migrations(ctx, Base.metadata).upgrade_ops.as_diffs()


def test_datenbankbenutzer_heisst_nicht_wie_das_schema(engine):
    """Die eigentliche Ursache, als Zusicherung festgehalten.

    Heisst der Benutzer wie das Schema, wird das Schema über ``"$user"`` zum
    Standardschema -- und jede erzeugte Migration enthält unbrauchbare
    Fremdschlüssel-Operationen.
    """
    with engine.connect() as con:
        benutzer = con.execute(text("SELECT current_user")).scalar_one()
        standardschema = con.dialect.default_schema_name
    assert benutzer != SCHEMA, (
        f"Der Datenbankbenutzer heisst {benutzer!r} wie das Schema. "
        "Das macht das Schema zum Standardschema der Verbindung und bringt "
        "Alembics Autogenerate durcheinander -- siehe Modul-Dokumentation."
    )
    assert standardschema != SCHEMA


def test_modelle_und_datenbank_stimmen_ueberein(engine):
    """Nach dem Anlegen der Tabellen darf Autogenerate nichts mehr finden.

    Schlägt der Test fehl, ist entweder ein Modell geändert worden, ohne eine
    Migration zu schreiben -- oder die Konfiguration in migrations/env.py erzeugt
    wieder Scheinunterschiede.
    """
    with engine.connect() as con:
        unterschiede = _diffs(con)
    assert unterschiede == [], (
        "Modelle und Datenbankschema laufen auseinander:\n  "
        + "\n  ".join(str(d) for d in unterschiede)
    )


def test_keine_fremdschluessel_scheinaenderungen(engine):
    """Gezielt auf den gefundenen Fehler.

    Fremdschlüssel-Operationen in einem frisch erzeugten Diff sind das Kennzeichen
    des Problems -- die Modelle sind unverändert, trotzdem will Alembic sie
    löschen und neu anlegen.
    """
    with engine.connect() as con:
        unterschiede = _diffs(con)
    fk_ops = [d for d in unterschiede if isinstance(d, tuple) and "fk" in str(d[0])]
    assert fk_ops == [], f"Scheinbare Fremdschlüsseländerungen: {fk_ops}"


@pytest.mark.parametrize(
    "tabelle",
    ["station", "measurement", "hourly", "dwd_hourly", "model", "forecast"],
)
def test_tabellen_liegen_im_schema_wetter(engine, tabelle):
    """Nichts von Alembic darf versehentlich in public oder app landen."""
    with engine.connect() as con:
        vorhanden = con.execute(
            text(
                "SELECT count(*) FROM pg_tables "
                "WHERE schemaname = :s AND tablename = :t"
            ),
            {"s": SCHEMA, "t": tabelle},
        ).scalar_one()
    assert vorhanden == 1
