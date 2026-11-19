"""add fit_predictions.details: how each score was reached, as JSON

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-18 19:00:21.845954

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("fit_predictions", sa.Column("details", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("fit_predictions", "details")
