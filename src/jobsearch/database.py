"""SQLite engine and sessions. Every connection uses WAL mode, a busy timeout, and foreign keys.

Transactions are started by SQLAlchemy, not by the sqlite3 driver. The driver's default
commits before DDL and starts no transaction before a SELECT, so a migration that fails
halfway would keep the DDL it had already run, and a read-then-write could act on data
another writer had changed in between. Every transaction here begins with an explicit
BEGIN; `session_scope` uses BEGIN IMMEDIATE, which takes the write lock before the
first read, so two writers run one after the other (waiting up to the busy timeout).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import URL, Connection, Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from jobsearch.config import DatabaseConfig

# Execution option naming how a transaction begins: DEFERRED (SQLite's default) or IMMEDIATE.
_BEGIN_MODE = "jobsearch_sqlite_begin"


def database_url(path: Path) -> URL:
    # URL.create keeps characters such as "?" and "#" in the path as part of the file name.
    return URL.create("sqlite", database=str(path))


def make_engine(config: DatabaseConfig) -> Engine:
    config.path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url(config.path))

    @event.listens_for(engine, "connect")
    def _configure_connection(dbapi_connection: Any, connection_record: Any) -> None:
        # Autocommit at the driver level, so only the BEGIN below starts transactions.
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={int(config.busy_timeout_ms)}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin(connection: Connection) -> None:
        mode = connection.get_execution_options().get(_BEGIN_MODE, "DEFERRED")
        connection.exec_driver_sql(f"BEGIN {mode}")

    return engine


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """One write transaction: commit on success, roll back on any error.

    It begins IMMEDIATE, taking the write lock before its first read, so another writer
    can't change what this one read before it writes.
    """
    factory = sessionmaker(
        engine.execution_options(**{_BEGIN_MODE: "IMMEDIATE"}), expire_on_commit=False
    )
    with factory() as session, session.begin():
        yield session
