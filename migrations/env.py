"""Alembic environment. The database path comes from the app config, never from alembic.ini.

Programmatic callers (the CLI and tests) pass the DatabaseConfig in
`config.attributes["database"]`; plain `alembic` commands use config/config.yaml.
"""

from alembic import context

from jobsearch.config import DatabaseConfig, load_config
from jobsearch.database import make_engine
from jobsearch.models.tables import Base

target_metadata = Base.metadata


def run_migrations_online() -> None:
    database = context.config.attributes.get("database")
    if not isinstance(database, DatabaseConfig):
        database = load_config().database
    engine = make_engine(database)
    with engine.connect() as connection:
        # Batch mode lets later migrations alter SQLite tables (copy-and-move).
        # SQLite's DDL is transactional once the driver stops committing around it (see
        # jobsearch.database), so each migration's DDL, data changes and alembic_version
        # update commit together or roll back together.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            transactional_ddl=True,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations aren't supported; run against the database.")
run_migrations_online()
