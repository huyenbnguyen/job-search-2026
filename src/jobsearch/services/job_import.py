"""Import staged `JobFetchRecord`s (JSONL) into the database.

Safe to rerun: jobs are matched on `job_key` and fetch attempts on (job, run ID), so
importing the same file twice adds nothing. Run it inside `session_scope`, which takes
SQLite's write lock before the first read: two imports (or an import and `db
set-company`) then run one after the other, so each lookup, company check, and
first-description check sees the latest committed rows and can't be overtaken before its
write. The unique constraints on `job_key` and (job, run ID) remain as a backstop.

A job keeps the first description fetched. Later text that differs is counted and its
hash stays on the fetch attempt, but the stored description never changes, so labels
and predictions keep referring to it.

`jobs.company` holds the best-known company. A record's company replaces it only
when it comes from a more reliable source (see CompanySource), so a URL hint never
replaces a posting's own name, and nothing replaces a name the user entered.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.models.tables import Job, JobFetchAttempt


@dataclass
class ImportSummary:
    records: int = 0
    jobs_created: int = 0
    descriptions_added: int = 0
    # Fetched text that differed from the job's stored description.
    descriptions_differed: int = 0
    attempts_created: int = 0
    job_companies_updated: int = 0


def read_records(path: Path) -> Iterator[JobFetchRecord]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield JobFetchRecord.model_validate_json(line)
            except ValueError as exc:
                raise ValueError(f"{path}:{line_number}: invalid record") from exc


def import_records(
    session: Session, records: Iterable[JobFetchRecord], *, applied: bool
) -> ImportSummary:
    summary = ImportSummary()
    for record in records:
        summary.records += 1
        job = _get_or_create_job(session, record, applied, summary)
        if (
            record.company
            and record.company_source
            and set_job_company(job, record.company, record.company_source)
        ):
            summary.job_companies_updated += 1
        _store_description(job, record, summary)
        _create_attempt_if_new(session, job, record, summary)
    return summary


def _get_or_create_job(
    session: Session, record: JobFetchRecord, applied: bool, summary: ImportSummary
) -> Job:
    job = session.scalar(select(Job).where(Job.job_key == record.job_key))
    if job is None:
        # The same URL can get a better key later, when a job board identifies a career page.
        job = session.scalar(select(Job).where(Job.source_url == record.source_url).limit(1))
        if job is not None and record.status is FetchStatus.FETCHED:
            job.job_key = record.job_key
            job.source = record.source
    if job is None:
        job = Job(
            job_key=record.job_key,
            source=record.source,
            source_url=record.source_url,
            applied=applied,
            first_seen_at=record.retrieved_at.astimezone(UTC),
        )
        session.add(job)
        session.flush()
        summary.jobs_created += 1
    elif applied and not job.applied:
        job.applied = True
    return job


def set_job_company(job: Job, company: str, source: CompanySource) -> bool:
    """Set the job's company if `source` beats the current one. Returns whether it changed.

    A user-entered name always applies, and only a user can replace one.
    """
    current = CompanySource(job.company_source) if job.company_source else None
    outranked = (
        job.company is not None
        and current is not None
        and source is not CompanySource.USER
        and source.rank <= current.rank
    )
    if outranked or (job.company == company and current is source):
        return False
    job.company = company
    job.company_source = source.value
    return True


def _store_description(job: Job, record: JobFetchRecord, summary: ImportSummary) -> None:
    if record.status is not FetchStatus.FETCHED:
        return
    if job.description_hash is None:
        job.title = record.title
        job.description_text = record.description_text
        job.description_hash = record.description_hash
        job.posting_url = record.posting_url
        job.retrieved_at = record.retrieved_at.astimezone(UTC)
        summary.descriptions_added += 1
    elif job.description_hash != record.description_hash:
        summary.descriptions_differed += 1


def _create_attempt_if_new(
    session: Session, job: Job, record: JobFetchRecord, summary: ImportSummary
) -> None:
    exists = session.scalar(
        select(JobFetchAttempt.id).where(
            JobFetchAttempt.job_id == job.id, JobFetchAttempt.run_id == record.run_id
        )
    )
    if exists is not None:
        return
    session.add(
        JobFetchAttempt(
            job_id=job.id,
            description_hash=record.description_hash,
            run_id=record.run_id,
            status=record.status.value,
            detail=record.detail,
            source_url=record.source_url,
            attempted_at=record.retrieved_at.astimezone(UTC),
        )
    )
    session.flush()
    summary.attempts_created += 1
