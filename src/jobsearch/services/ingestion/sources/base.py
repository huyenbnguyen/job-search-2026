"""Common interface for job sources: one adapter per source."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

import httpx

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord, utc_now
from jobsearch.services.ingestion.fetch import SafeFetcher

# Query parameters that only track how a link was reached, not which job it is.
_TRACKING_PARAMS = frozenset(
    {
        "alternatechannel", "ebp", "fbclid", "gclid", "gh_src", "lever-origin",
        "lever-source", "lever-source[]", "ref", "refid", "src", "source", "trackingid",
        "trk",
    }
)  # fmt: skip


@dataclass(frozen=True)
class FetchContext:
    fetcher: SafeFetcher
    run_id: str
    user_agent: str
    now: Callable[[], datetime] = field(default=utc_now)


class SourceAdapter(ABC):
    name: ClassVar[str]

    @abstractmethod
    def matches(self, url: httpx.URL) -> bool: ...

    @abstractmethod
    def job_key(self, url: httpx.URL) -> str:
        """A stable dedupe key for the job at `url`, computed without network access."""

    @abstractmethod
    def fetch(self, source_url: str, url: httpx.URL, ctx: FetchContext) -> JobFetchRecord: ...

    def record(
        self,
        ctx: FetchContext,
        *,
        source_url: str,
        job_key: str,
        status: FetchStatus,
        **fields: Any,
    ) -> JobFetchRecord:
        if fields.get("company") and "company_source" not in fields:
            fields["company_source"] = CompanySource.JOB_POSTING
        elif not fields.get("company"):
            fields.pop("company", None)
            fields.pop("company_source", None)
        return JobFetchRecord(
            run_id=ctx.run_id,
            source_url=source_url,
            job_key=job_key,
            source=self.name,
            status=status,
            retrieved_at=ctx.now(),
            **fields,
        )


def normalize_url(url: httpx.URL) -> str:
    """Lowercased scheme and host, no fragment, no tracking parameters, sorted query."""
    params = sorted(
        (key, value)
        for key, value in url.params.multi_items()
        if key.lower() not in _TRACKING_PARAMS and not key.lower().startswith("utm_")
    )
    path = url.path.rstrip("/") or "/"
    normalized = url.copy_with(path=path, fragment=None, query=None)
    if params:
        normalized = normalized.copy_with(params=params)
    return str(normalized)


def text_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
