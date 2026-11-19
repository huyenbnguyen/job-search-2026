"""add the best-known company to jobs, with where it came from

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-23 20:11:25.706180

Backfills `jobs.company` from each job's latest description version, marked as
`job_posting`, and points `jobs_overview` at the job-level company so jobs whose
details couldn't be fetched can still show one.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LATEST_VERSION = """
    SELECT v.id FROM job_versions AS v
    WHERE v.job_id = j.id
    ORDER BY v.retrieved_at DESC, v.id DESC
    LIMIT 1
"""
_LATEST_ATTEMPT = """
    SELECT a.id FROM job_fetch_attempts AS a
    WHERE a.job_id = j.id
    ORDER BY a.attempted_at DESC, a.id DESC
    LIMIT 1
"""


def _overview(company_columns: str) -> str:
    return f"""
CREATE VIEW jobs_overview AS
SELECT
    j.id AS job_id,
    j.job_key,
    j.source,
    j.applied,
    j.source_url,
    v.title,
    {company_columns},
    v.posting_url,
    v.description_text,
    v.retrieved_at,
    a.status AS last_status,
    a.detail AS last_detail,
    a.attempted_at AS last_attempted_at
FROM jobs AS j
LEFT JOIN job_versions AS v ON v.id = ({_LATEST_VERSION})
LEFT JOIN job_fetch_attempts AS a ON a.id = ({_LATEST_ATTEMPT})
"""


def upgrade() -> None:
    # SQLite can't alter a table that a view depends on; drop and recreate the view.
    op.execute("DROP VIEW jobs_overview")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("company", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("company_source", sa.String(length=32), nullable=True))
    op.execute(
        f"""
        UPDATE jobs AS j
        SET company = (
                SELECT v.company FROM job_versions AS v WHERE v.id = ({_LATEST_VERSION})
            ),
            company_source = 'job_posting'
        WHERE (SELECT v.company FROM job_versions AS v WHERE v.id = ({_LATEST_VERSION}))
            IS NOT NULL
        """
    )
    op.execute(_overview("j.company,\n    j.company_source"))


def downgrade() -> None:
    op.execute("DROP VIEW jobs_overview")
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        batch_op.drop_column("company_source")
        batch_op.drop_column("company")
    op.execute(_overview("v.company"))
