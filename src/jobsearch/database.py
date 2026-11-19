"""SQLite engine and sessions. Every connection uses WAL mode, a busy timeout, and foreign keys."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from jobsearch.config import DatabaseConfig


def database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def make_engine(config: DatabaseConfig) -> Engine:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url(config.path))

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(config.busy_timeout_ms)}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One transaction: commit on success, roll back on any error."""
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session, session.begin():
        yield session
