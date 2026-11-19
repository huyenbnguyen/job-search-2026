"""Fetch job details for a CSV of applied-job URLs and stage them as JSONL.

Each output line is one `JobFetchRecord`, the shape the later SQLite import reads.
Every URL gets a record, including the ones that couldn't be fetched, so the
import also knows which jobs need the paste-text fallback.
"""

import csv
import logging
import os
import tempfile
import time
from collections.abc import Iterable, Iterator
from pathlib import Path

import httpx

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.fetch import FetchError
from jobsearch.services.ingestion.sources import FetchContext, SourceAdapter
from jobsearch.services.ingestion.sources.company_from_url import company_from_url

logger = logging.getLogger(__name__)


def read_job_urls(path: Path) -> list[str]:
    """Return the URL in the first column of each non-blank row, in file order."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [row[0].strip() for row in csv.reader(handle) if row and row[0].strip()]


def fetch_jobs(
    urls: Iterable[str], adapters: list[SourceAdapter], ctx: FetchContext
) -> Iterator[JobFetchRecord]:
    """Yield one record per distinct job. A failure on one URL never stops the run."""
    seen_keys: set[str] = set()
    for source_url in urls:
        record = _fetch_one(source_url, adapters, ctx, seen_keys)
        if record is None or record.job_key in seen_keys:
            continue
        seen_keys.add(record.job_key)
        yield record


def _fetch_one(
    source_url: str, adapters: list[SourceAdapter], ctx: FetchContext, seen_keys: set[str]
) -> JobFetchRecord | None:
    try:
        url = httpx.URL(source_url)
    except httpx.InvalidURL:
        url = None
    adapter = next((a for a in adapters if url is not None and a.matches(url)), None)
    if url is None or adapter is None:
        return _failed(ctx, source_url, f"invalid:{source_url}", "unknown", "not an http(s) URL")

    key = adapter.job_key(url)
    if key in seen_keys:
        return None
    started = time.monotonic()
    try:
        record = adapter.fetch(source_url, url, ctx)
        error_category = None
    except FetchError as exc:
        record = _failed(ctx, source_url, key, adapter.name, str(exc))
        error_category = exc.category
    except Exception as exc:  # One bad response must not end the run.
        record = _failed(ctx, source_url, key, adapter.name, f"unexpected {type(exc).__name__}")
        error_category = "unexpected_error"
        logger.exception("unexpected error while fetching", extra={"job_key": key})
    if record.company is None and (hint := company_from_url(url)):
        record = record.model_copy(update={"company": hint, "company_source": CompanySource.URL})
    logger.info(
        "job processed",
        extra={
            "job_key": record.job_key,
            "source": record.source,
            "status": record.status.value,
            "company_source": record.company_source,
            "error_category": error_category,
            "latency_ms": round((time.monotonic() - started) * 1000),
        },
    )
    return record


def _failed(
    ctx: FetchContext, source_url: str, key: str, source: str, detail: str
) -> JobFetchRecord:
    return JobFetchRecord(
        run_id=ctx.run_id,
        source_url=source_url,
        job_key=key,
        source=source,
        status=FetchStatus.FAILED,
        retrieved_at=ctx.now(),
        detail=detail,
    )


def write_jsonl(records: Iterable[JobFetchRecord], path: Path) -> int:
    """Write records atomically: the file is replaced only after every record is written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(record.model_dump_json() + "\n")
                count += 1
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return count
