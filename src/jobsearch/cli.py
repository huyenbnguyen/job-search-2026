"""Command-line entry point. Keep commands thin; logic lives in services/."""

import uuid
from collections import Counter
from itertools import islice
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from jobsearch.config import load_config
from jobsearch.models.job import FetchStatus, JobFetchRecord
from jobsearch.observability.logging import configure_logging
from jobsearch.services.ingestion.applied_jobs import fetch_jobs, read_job_urls, write_jsonl
from jobsearch.services.ingestion.fetch import SafeFetcher
from jobsearch.services.ingestion.sources import FetchContext, default_adapters
from jobsearch.services.ingestion.sources.base import normalize_url

app = typer.Typer(no_args_is_help=True)
console = Console()
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
