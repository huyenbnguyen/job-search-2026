"""add jobs.got_interviewed, recorded by the user

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema. Existing jobs start as false, and so does any new job."""
    op.add_column(
        "jobs",
        sa.Column("got_interviewed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("jobs", "got_interviewed")
