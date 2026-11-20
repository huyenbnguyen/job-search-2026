"""Command-line entry point. Keep commands thin; logic lives in services/."""

import logging
import uuid
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from jobsearch.config import DatabaseConfig, load_config
from jobsearch.database import make_engine, session_scope
from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.models.tables import FitPrediction, HumanLabel, Job, ResumeVersion
from jobsearch.observability.logging import configure_logging
from jobsearch.services.ingestion.applied_jobs import fetch_jobs, read_job_urls, write_jsonl
from jobsearch.services.ingestion.fetch import SafeFetcher
from jobsearch.services.ingestion.sources import FetchContext, default_adapters
from jobsearch.services.ingestion.sources.base import normalize_url
from jobsearch.services.job_import import import_records, read_records, set_job_company
from jobsearch.services.migrations import (
    SchemaOutOfDateError,
    check_schema_current,
    upgrade_database,
)
from jobsearch.services.profiles import ResumeParseError, parse_resume, save_resume_version
from jobsearch.services.scoring.keyword_baseline import run_keyword_baseline

app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True, help="Local SQLite database.")
app.add_typer(db_app, name="db")
resume_app = typer.Typer(no_args_is_help=True, help="Your resume, stored as versioned text.")
app.add_typer(resume_app, name="resume")
score_app = typer.Typer(no_args_is_help=True, help="Score jobs against your resume.")
app.add_typer(score_app, name="score")
console = Console()
_log = logging.getLogger("jobsearch.cli")
ConfigOption = Annotated[Path | None, typer.Option("--config", help="Config YAML")]


@app.callback()
def main() -> None:
    """Local job search and fit-scoring tool."""


@app.command()
def fetch_applied(
    csv_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="CSV of job URLs")],
    out: Annotated[Path, typer.Option(help="JSONL output file")] = Path("data/applied_jobs.jsonl"),
    config_path: ConfigOption = None,
    limit: Annotated[int | None, typer.Option(min=1, help="Only the first N URLs")] = None,
) -> None:
    """Fetch job details for applied-job URLs and stage them as JSONL for the database import."""
    config = load_config(config_path)
    run_id = uuid.uuid4().hex
    configure_logging(config.logging.file, run_id)
    fetch_config = config.ingestion.fetch

    urls = read_job_urls(csv_path)
    if limit is not None:
        urls = urls[:limit]
    console.print(f"Run [bold]{run_id}[/]: {len(urls)} URLs from {csv_path}")

    records: list[JobFetchRecord] = []
    adapters = default_adapters(respect_robots_txt=fetch_config.respect_robots_txt)
    with SafeFetcher(fetch_config) as fetcher:
        ctx = FetchContext(fetcher=fetcher, run_id=run_id, user_agent=fetch_config.user_agent)
        with console.status("Fetching...") as status:
            for record in fetch_jobs(urls, adapters, ctx):
                records.append(record)
                status.update(f"Fetching... {len(records)} jobs done")
    count = write_jsonl(records, out)

    _print_summary(records)
    console.print(f"Wrote {count} records to {out}")


def _print_summary(records: list[JobFetchRecord]) -> None:
    counts = Counter(record.status for record in records)
    summary = Table(title="Results")
    summary.add_column("Status")
    summary.add_column("Jobs", justify="right")
    for fetch_status in FetchStatus:
        summary.add_row(fetch_status.value, str(counts.get(fetch_status, 0)))
    console.print(summary)

    missing = [record for record in records if record.status is not FetchStatus.FETCHED]
    if not missing:
        return
    table = Table(title="Needs the paste-text fallback")
    table.add_column("Status")
    table.add_column("URL", overflow="fold", max_width=70)
    table.add_column("Reason", overflow="fold")
    for record in islice(missing, 200):
        table.add_row(record.status.value, _display_url(record.source_url), record.detail or "")
    console.print(table)


def _display_url(url: str) -> str:
    try:
        return normalize_url(httpx.URL(url))
    except httpx.InvalidURL:
        return url


def _current_engine(database: DatabaseConfig) -> Engine:
    """An engine for the database, or exit if its schema needs `db upgrade` first."""
    engine = make_engine(database)
    try:
        check_schema_current(engine)
    except SchemaOutOfDateError as exc:
        engine.dispose()
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    return engine


@db_app.command("upgrade")
def db_upgrade(config_path: ConfigOption = None) -> None:
    """Create the database if needed and apply all migrations."""
    database = load_config(config_path).database
    upgrade_database(database)
    console.print(f"Database is up to date: {database.path}")


@db_app.command("import-jobs")
def db_import_jobs(
    jsonl_path: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="JSONL from fetch-applied")
    ] = Path("data/applied_jobs.jsonl"),
    applied: Annotated[bool, typer.Option(help="Mark these jobs as ones you applied to")] = True,
    config_path: ConfigOption = None,
) -> None:
    """Import fetched job records. Safe to rerun: existing rows aren't duplicated."""
    database = load_config(config_path).database
    engine = _current_engine(database)
    with session_scope(engine) as session:
        summary = import_records(session, read_records(jsonl_path), applied=applied)
    engine.dispose()
    console.print(
        f"Read {summary.records} records: {summary.jobs_created} new jobs, "
        f"{summary.descriptions_added} new job descriptions, "
        f"{summary.descriptions_differed} differed from the stored text (kept the stored text), "
        f"{summary.attempts_created} new fetch results, "
        f"{summary.job_companies_updated} job companies set or improved."
    )
    console.print(f"Database: {database.path}")


@db_app.command("set-company")
def db_set_company(
    job_id: Annotated[int, typer.Argument(help="job_id from jobs_overview")],
    company: Annotated[str, typer.Argument(help="Company name")],
    config_path: ConfigOption = None,
) -> None:
    """Set a job's company by hand. Automatic sources never replace it."""
    name = company.strip()
    if not name:
        console.print("[red]Company name can't be empty.[/]")
        raise typer.Exit(1)
    database = load_config(config_path).database
    engine = _current_engine(database)
    with session_scope(engine) as session:
        job = session.get(Job, job_id)
        if job is None:
            console.print(f"[red]No job with job_id {job_id}.[/]")
            raise typer.Exit(1)
        set_job_company(job, name, CompanySource.USER)
        console.print(f"Job {job_id} ({job.job_key}): company set to {name!r}.")
    engine.dispose()


@resume_app.command("load")
def resume_load(
    pdf_path: Annotated[
        Path, typer.Argument(exists=True, dir_okay=False, help="Text-based PDF resume")
    ],
    config_path: ConfigOption = None,
) -> None:
    """Extract your resume's text and store it as a new version, unless it's already stored."""
    try:
        parsed = parse_resume(pdf_path)
    except ResumeParseError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    database = load_config(config_path).database
    engine = _current_engine(database)
    with session_scope(engine) as session:
        version, created = save_resume_version(session, parsed)
        summary = (
            f"{'Stored new' if created else 'Already stored as'} resume version {version.id} "
            f"({version.text_hash[:12]}): {parsed.page_count} pages, "
            f"{len(parsed.text.splitlines())} lines, {len(parsed.text)} characters."
        )
    engine.dispose()
    console.print(summary)
    console.print(f"Check the extracted text with: jobsearch resume show {version.id}")


@resume_app.command("list")
def resume_list(config_path: ConfigOption = None) -> None:
    """List stored resume versions, newest first."""
    engine = _current_engine(load_config(config_path).database)
    table = Table(title="Resume versions")
    for column in ("ID", "Version", "Pages", "Lines", "Loaded (UTC)"):
        table.add_column(column)
    table.add_column("File", overflow="fold")
    with session_scope(engine) as session:
        for version in session.scalars(select(ResumeVersion).order_by(ResumeVersion.id.desc())):
            table.add_row(
                str(version.id),
                version.text_hash[:12],
                str(version.page_count),
                str(len(version.text.splitlines())),
                f"{version.created_at:%Y-%m-%d %H:%M}",
                version.source_filename,
            )
    engine.dispose()
    console.print(table)


@resume_app.command("show")
def resume_show(
    version_id: Annotated[
        int | None, typer.Argument(help="ID from `resume list`; the newest by default")
    ] = None,
    config_path: ConfigOption = None,
) -> None:
    """Print a stored resume's extracted text, exactly as scoring will see it."""
    engine = _current_engine(load_config(config_path).database)
    with session_scope(engine) as session:
        query = select(ResumeVersion).order_by(ResumeVersion.id.desc()).limit(1)
        if version_id is not None:
            query = select(ResumeVersion).where(ResumeVersion.id == version_id)
        version = session.scalar(query)
        text = version.text if version else None
    engine.dispose()
    if text is None:
        console.print("[red]No resume found. Load one with `jobsearch resume load <pdf>`.[/]")
        raise typer.Exit(1)
    # Plain output: resume text may contain [brackets] that Rich would read as markup.
    typer.echo(text)


@score_app.command("baseline")
def score_baseline(
    resume_id: Annotated[
        int | None, typer.Option("--resume", help="ID from `resume list`; the newest by default")
    ] = None,
    config_path: ConfigOption = None,
) -> None:
    """Score every job with a description by keyword overlap with your resume (no LLM)."""
    config = load_config(config_path)
    run_id = uuid.uuid4().hex
    configure_logging(config.logging.file, run_id)
    engine = _current_engine(config.database)
    with session_scope(engine) as session:
        query = select(ResumeVersion).order_by(ResumeVersion.id.desc()).limit(1)
        if resume_id is not None:
            query = select(ResumeVersion).where(ResumeVersion.id == resume_id)
        resume = session.scalar(query)
        if resume is None:
            engine.dispose()
            console.print("[red]No resume found. Load one with `jobsearch resume load <pdf>`.[/]")
            raise typer.Exit(1)
        summary = run_keyword_baseline(
            session,
            resume,
            config.scoring.keyword_baseline,
            config.scoring.rubric_version,
            run_id,
        )
        rows = _labeled_baseline_rows(session, summary.current_prediction_ids)
        resume_label = f"{resume.id} ({resume.text_hash[:12]})"
    engine.dispose()
    _log.info(
        "keyword baseline run finished",
        extra={
            "resume_version": resume_label,
            "jobs_scored": summary.jobs_scored,
            "jobs_unchanged": summary.jobs_unchanged,
            "jobs_without_skills": len(summary.jobs_without_skills),
        },
    )

    console.print(
        f"Run [bold]{run_id}[/], resume version {resume_label}: "
        f"{summary.jobs_scored} jobs scored, "
        f"{summary.jobs_unchanged} already scored with the same inputs (skipped)."
    )
    if summary.jobs_without_skills:
        ids = ", ".join(map(str, summary.jobs_without_skills))
        console.print(f"No known skills in the description, not scored: job_id {ids}")
    if rows:
        _print_baseline_table(rows)
    hidden = len(summary.current_prediction_ids) - len(rows)
    if hidden:
        console.print(
            f"Scores for {hidden} jobs you haven't labeled are hidden. Label them first, "
            "so the baseline can't influence your score."
        )


def _labeled_baseline_rows(session: Session, prediction_ids: list[int]) -> list[tuple[str, ...]]:
    """The scores for this run's inputs, for jobs the user has already labeled only."""
    rows: list[tuple[str, ...]] = []
    for prediction_id in prediction_ids:
        prediction = session.get(FitPrediction, prediction_id)
        assert prediction is not None
        label = session.scalar(
            select(HumanLabel)
            .where(HumanLabel.job_id == prediction.job_id)
            .order_by(HumanLabel.labeled_at.desc(), HumanLabel.id.desc())
            .limit(1)
        )
        if label is None:
            continue
        job = session.get(Job, prediction.job_id)
        assert job is not None
        details = prediction.details or {}
        rows.append(
            (
                str(job.id),
                job.company or "",
                job.title or "",
                str(label.fit_score),
                str(prediction.fit_score),
                f"{details.get('matched_count', 0)}/{details.get('job_skill_count', 0)}",
            )
        )
    return rows


def _print_baseline_table(rows: list[tuple[str, ...]]) -> None:
    table = Table(title="Keyword baseline on jobs you've labeled")
    table.add_column("Job", justify="right")
    table.add_column("Company", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("You", justify="right")
    table.add_column("Baseline", justify="right")
    table.add_column("Skills matched", justify="right")
    for row in rows:
        table.add_row(*row)
    console.print(table)
