"""fit scores must be the whole numbers 1-5

`BETWEEN 1 AND 5` accepted a value such as 3.5, because SQLite stores a non-whole number
as REAL even in an INTEGER column. `IN (1, 2, 3, 4, 5)` accepts only whole numbers.
Stops with an error if a stored score isn't one of them.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-18 19:08:43.487576

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("human_labels", "fit_predictions")
_WHOLE = "fit_score IN (1, 2, 3, 4, 5)"
_RANGE = "fit_score BETWEEN 1 AND 5"


def _replace_checks(condition: str) -> None:
    # Rebuilding a table fails while jobs_overview refers to it, so the view is dropped
    # and recreated from its exact stored SQL.
    connection = op.get_bind()
    view_sql = connection.execute(
        sa.text("SELECT sql FROM sqlite_master WHERE type = 'view' AND name = 'jobs_overview'")
    ).scalar_one()
    op.execute("DROP VIEW jobs_overview")
    for table in _TABLES:
        name = f"ck_{table}_fit_score"
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(name, type_="check")
            batch_op.create_check_constraint(name, condition)
    op.execute(view_sql)


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    for table in _TABLES:
        bad = connection.execute(
            sa.text(f"SELECT COUNT(*) FROM {table} WHERE NOT ({_WHOLE})")
        ).scalar()
        if bad:
            raise RuntimeError(
                f"{bad} rows in {table} have a fit_score that isn't 1, 2, 3, 4 or 5. "
                "Correct them before upgrading."
            )
    _replace_checks(_WHOLE)


def downgrade() -> None:
    """Downgrade schema."""
    _replace_checks(_RANGE)
