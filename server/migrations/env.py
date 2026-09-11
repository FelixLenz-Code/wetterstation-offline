"""Alembic-Umgebung.

Legt das Schema ``wetter`` bei Bedarf selbst an -- eine frisch hochgefahrene
Postgres-Instanz aus dem Compose-Stack hat es noch nicht, und ein Fehlschlag beim
allerersten Start wäre eine unnötige Hürde.

Eine Falle, die hier viel Zeit gekostet hat und deshalb festgehalten gehört: der
**Datenbankbenutzer darf nicht so heißen wie das Schema**. Postgres hat den
Standard-Suchpfad ``"$user", public``; heißt der Benutzer ``wetter`` und das Schema
auch, dann ist ``wetter`` das Standardschema der Verbindung. SQLAlchemy normalisiert
dann bei der Reflexion ``wetter.forecast`` zu ``forecast`` ohne Schema, während die
Metadaten ``wetter.forecast`` sagen. Alembic hält beides für verschiedene Tabellen
und schreibt bei *jedem* Autogenerate dieselben sechs Fremdschlüssel als entfernt
und wieder hinzugefügt in eine neue Migration -- mit ``drop_constraint``-Aufrufen
ohne Schemaangabe, die zur Laufzeit scheitern.

Deshalb heißt der Benutzer ``wetterapp``. Siehe compose.yml.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from wetter.db.models import SCHEMA, Base
from wetter.db.session import DbSettings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", DbSettings().database_url)
target_metadata = Base.metadata


#: Verwaltungstabellen der beiden Migrationssysteme. Keines der beiden darf das
#: andere sehen, sonst schreibt es ein DROP dafür in seine Migration.
VERWALTUNGSTABELLEN = {"alembic_version", "_prisma_migrations"}


def include_name(name, type_, parent_names) -> bool:
    """Beschränkt die Reflexion auf das Schema ``wetter``.

    Ohne die Einschränkung sieht Alembic auch das Schema ``app``, das Prisma gehört,
    und meldet dessen Tabellen als entfernt.
    """
    if type_ == "schema":
        return name == SCHEMA
    return True


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Filtert die Verwaltungstabellen beider Migrationssysteme aus."""
    return name not in VERWALTUNGSTABELLEN


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
        include_name=include_name,
        version_table_schema=SCHEMA,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_object=include_object,
            include_name=include_name,
            version_table_schema=SCHEMA,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
