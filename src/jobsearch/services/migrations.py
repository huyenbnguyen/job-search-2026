"""Run Alembic migrations against the configured database."""

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from jobsearch.config import DatabaseConfig
from jobsearch.paths import PROJECT_ROOT as _PROJECT_ROOT


def upgrade_database(database: DatabaseConfig, revision: str = "head") -> None:
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.attributes["database"] = database
    command.upgrade(config, revision)


class SchemaOutOfDateError(RuntimeError):
    pass


def check_schema_current(engine: Engine) -> None:
    """Raise SchemaOutOfDateError unless the database is at the latest migration."""
    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != head:
        raise SchemaOutOfDateError(
            f"database schema is at {current or 'nothing'}, expected {head}; "
            "run `jobsearch db upgrade` first"
        )
