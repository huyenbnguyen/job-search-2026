"""move each job's description onto jobs and drop job_versions

Stops with an error if any job has more than one version, because choosing one would
silently change which text its labels and predictions refer to.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-30 19:43:19.048892

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

_LATEST = """
    SELECT {alias}.id FROM {table} AS {alias}
    WHERE {alias}.job_id = j.id
    ORDER BY {alias}.{order_column} DESC, {alias}.id DESC
    LIMIT 1
"""


def _latest(table: str, alias: str, order_column: str) -> str:
    return _LATEST.format(table=table, alias=alias, order_column=order_column)


_LATEST_JOINS = f"""
LEFT JOIN job_fetch_attempts AS a ON a.id = ({_latest("job_fetch_attempts", "a2", "attempted_at")})
LEFT JOIN human_labels AS h ON h.id = ({_latest("human_labels", "h2", "labeled_at")})
LEFT JOIN fit_predictions AS p ON p.id = ({_latest("fit_predictions", "p2", "created_at")})
"""

_OVERVIEW_0003 = f"""
CREATE VIEW jobs_overview AS
SELECT
    j.id AS job_id,
    j.job_key,
    j.source,
    j.applied,
    j.source_url,
    v.title,
    j.company,
    j.company_source,
    h.fit_score AS human_fit_score,
    h.would_apply AS human_would_apply,
    p.fit_score AS ai_fit_score,
    v.posting_url,
    v.description_text,
    v.retrieved_at,
    a.status AS last_status,
    a.detail AS last_detail,
    a.attempted_at AS last_attempted_at
FROM jobs AS j
LEFT JOIN job_versions AS v ON v.id = ({_latest("job_versions", "v2", "retrieved_at")})
{_LATEST_JOINS}
"""

_OVERVIEW_0004 = f"""
CREATE VIEW jobs_overview AS
SELECT
    j.id AS job_id,
    j.job_key,
    j.source,
    j.applied,
    j.source_url,
    j.title,
    j.company,
    j.company_source,
    h.fit_score AS human_fit_score,
    h.would_apply AS human_would_apply,
    p.fit_score AS ai_fit_score,
    j.posting_url,
    j.description_text,
    j.retrieved_at,
    a.status AS last_status,
    a.detail AS last_detail,
    a.attempted_at AS last_attempted_at
FROM jobs AS j
{_LATEST_JOINS}
"""

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    repeated = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM "
            "(SELECT job_id FROM job_versions GROUP BY job_id HAVING COUNT(*) > 1)"
        )
    ).scalar()
    if repeated:
        raise RuntimeError(
            f"{repeated} jobs have more than one description version. "
            "Resolve them before dropping job_versions."
        )

    op.execute("DROP VIEW jobs_overview")
    op.add_column("jobs", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("description_text", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("description_hash", sa.String(length=64), nullable=True))
    op.add_column("jobs", sa.Column("posting_url", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE jobs SET (title, description_text, description_hash, posting_url, retrieved_at) = (
            SELECT v.title, v.description_text, v.description_hash, v.posting_url, v.retrieved_at
            FROM job_versions AS v WHERE v.job_id = jobs.id
        )
        """
    )

    op.add_column(
        "job_fetch_attempts", sa.Column("description_hash", sa.String(length=64), nullable=True)
    )
    op.execute(
        """
        UPDATE job_fetch_attempts SET description_hash = (
            SELECT v.description_hash FROM job_versions AS v
            WHERE v.id = job_fetch_attempts.job_version_id
        )
        """
    )
    with op.batch_alter_table("job_fetch_attempts", schema=None) as batch_op:
        batch_op.drop_column("job_version_id")
    with op.batch_alter_table("human_labels", schema=None) as batch_op:
        batch_op.drop_column("job_version_id")
    with op.batch_alter_table("fit_predictions", schema=None) as batch_op:
        batch_op.drop_column("job_version_id")

    op.drop_table("job_versions")
    op.execute(_OVERVIEW_0004)


def downgrade() -> None:
    """Downgrade schema. Each job's single description becomes a version with the job's ID."""
    op.execute("DROP VIEW jobs_overview")
    op.create_table(
        "job_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("description_hash", sa.String(length=64), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("description_text", sa.Text(), nullable=False),
        sa.Column("posting_url", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "description_hash"),
    )
    op.execute(
        """
        INSERT INTO job_versions (id, job_id, description_hash, title, company, description_text,
                                  posting_url, source_url, retrieved_at, run_id)
        SELECT j.id, j.id, j.description_hash, j.title, j.company, j.description_text,
               j.posting_url, j.source_url, j.retrieved_at,
               COALESCE((SELECT a.run_id FROM job_fetch_attempts AS a
                         WHERE a.job_id = j.id AND a.description_hash = j.description_hash
                         ORDER BY a.attempted_at, a.id LIMIT 1), 'unknown')
        FROM jobs AS j WHERE j.description_hash IS NOT NULL
        """
    )

    with op.batch_alter_table("job_fetch_attempts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("job_version_id", sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE job_fetch_attempts SET job_version_id = job_id
        WHERE description_hash = (SELECT j.description_hash FROM jobs AS j
                                  WHERE j.id = job_fetch_attempts.job_id)
        """
    )
    with op.batch_alter_table("job_fetch_attempts", schema=None) as batch_op:
        batch_op.create_foreign_key(
            "fk_job_fetch_attempts_job_version_id", "job_versions", ["job_version_id"], ["id"]
        )
        batch_op.drop_column("description_hash")

    for table in ("human_labels", "fit_predictions"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("job_version_id", sa.Integer(), nullable=True))
        op.execute(
            f"""
            UPDATE {table} SET job_version_id = job_id
            WHERE job_id IN (SELECT id FROM job_versions)
            """
        )
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.create_foreign_key(
                f"fk_{table}_job_version_id", "job_versions", ["job_version_id"], ["id"]
            )
            if table == "fit_predictions":
                batch_op.alter_column("job_version_id", existing_type=sa.Integer(), nullable=False)

    # Not a batch: recreating `jobs` would break the foreign keys of rows that reference it.
    for column in ("retrieved_at", "posting_url", "description_hash", "description_text", "title"):
        op.drop_column("jobs", column)
    op.execute(_OVERVIEW_0003)
