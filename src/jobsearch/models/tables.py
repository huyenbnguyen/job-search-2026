"""Database tables.

- `jobs`: one row per distinct job (unique `job_key`), with the best-known company and
  where that name came from (`company_source`, see CompanySource). It also holds the
  first description fetched, which is never overwritten, so every label and prediction
  for a job refers to the same text.
- `job_fetch_attempts`: every fetch outcome, including jobs that couldn't be fetched,
  so the paste-text fallback knows what's missing. Its `description_hash` shows when a
  posting's text later differed from the stored description.
- `human_labels`: the user's fit score (1-5) and would-apply answer, append-only. A
  re-scoring pass adds a row, so earlier labels stay for the consistency check.
- `fit_predictions`: model fit scores (1-5), kept apart from human labels, each tied to
  the job, resume version, model, prompt, and rubric that produced it.

`jobs_overview` shows the latest of each as `human_fit_score`, `human_would_apply`,
and `ai_fit_score`. Labeling must never show model scores before the user scores a job.
"""

from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {datetime: DateTime(timezone=True)}


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_key: Mapped[str] = mapped_column(String(512), unique=True)
    source: Mapped[str] = mapped_column(String(64))
    # The URL this job was first imported from.
    source_url: Mapped[str] = mapped_column(Text)
    # The user applied to this job. A weak positive signal, not a fit label.
    applied: Mapped[bool] = mapped_column(default=False)
    # Whether the user got an interview, set by the user. An outcome, not a fit label.
    got_interviewed: Mapped[bool] = mapped_column(default=False, server_default=false())
    first_seen_at: Mapped[datetime]
    company: Mapped[str | None] = mapped_column(Text)
    company_source: Mapped[str | None] = mapped_column(String(32))
    # Null until a fetch succeeds. Set together, once.
    title: Mapped[str | None] = mapped_column(Text)
    description_text: Mapped[str | None] = mapped_column(Text)
    description_hash: Mapped[str | None] = mapped_column(String(64))
    posting_url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None]

    fetch_attempts: Mapped[list["JobFetchAttempt"]] = relationship(back_populates="job")


class JobFetchAttempt(Base):
    __tablename__ = "job_fetch_attempts"
    __table_args__ = (
        UniqueConstraint("job_id", "run_id"),
        Index("ix_job_fetch_attempts_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    # The hash of the text this fetch returned, which may differ from the stored description.
    description_hash: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text)
    attempted_at: Mapped[datetime]

    job: Mapped[Job] = relationship(back_populates="fetch_attempts")


class HumanLabel(Base):
    __tablename__ = "human_labels"
    __table_args__ = (
        CheckConstraint("fit_score BETWEEN 1 AND 5", name="ck_human_labels_fit_score"),
        Index("ix_human_labels_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    fit_score: Mapped[int]
    would_apply: Mapped[bool]
    rubric_version: Mapped[str] = mapped_column(String(64))
    labeled_at: Mapped[datetime]
    # The user's reasoning for the fit score, in their own words. Optional.
    notes: Mapped[str | None] = mapped_column(Text)


class FitPrediction(Base):
    __tablename__ = "fit_predictions"
    __table_args__ = (
        CheckConstraint("fit_score BETWEEN 1 AND 5", name="ck_fit_predictions_fit_score"),
        Index("ix_fit_predictions_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    fit_score: Mapped[int]
    provider: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64))
    rubric_version: Mapped[str] = mapped_column(String(64))
    resume_version: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime]
