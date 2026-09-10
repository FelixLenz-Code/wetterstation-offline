"""Alembic-Umgebung.

Legt das Schema ``wetter`` bei Bedarf selbst an -- eine frisch hochgefahrene
Postgres-Instanz aus dem Compose-Stack hat es noch nicht, und ein Fehlschlag beim
allerersten Start wäre eine unnötige Hürde.
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


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Filtert aus, was Alembic nicht selbst verwalten soll.

    Ohne den Filter erkennt ein Autogenerate-Lauf mit ``include_schemas=True`` die
    eigene Verwaltungstabelle ``alembic_version`` als "entfernt" und schreibt ein
    DROP dafür in die Migration -- die dann ihre eigene Buchführung zerstört.

    Ebenso bleibt alles ausserhalb des Schemas ``wetter`` unangetastet: das Schema
    ``app`` gehört Prisma und der PWA.
    """
    if name == "alembic_version":
        return False
    schema = getattr(obj, "schema", None)
    if type_ == "table" and schema not in (SCHEMA, None):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_object=include_object,
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
            version_table_schema=SCHEMA,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
