"""Greenhouse, through its public Job Board API (boards-api.greenhouse.io).

The US API also serves boards hosted on job-boards.eu.greenhouse.io.
"""

import html
import re
from urllib.parse import quote

import httpx

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.fetch import FetchError
from jobsearch.services.ingestion.html_utils import html_to_text
from jobsearch.services.ingestion.sources.base import FetchContext, SourceAdapter, text_or_none

_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    }
)
_API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
_BOARD_API = "https://boards-api.greenhouse.io/v1/boards/{board}"
_JOB_PATH = re.compile(r"^/(?P<board>[\w-]+)/jobs/(?P<job_id>\d+)")


def parse_greenhouse_url(url: httpx.URL) -> tuple[str, str] | None:
    """Return (board, job_id) for a Greenhouse job URL, or None."""
    if url.host not in _HOSTS:
        return None
    match = _JOB_PATH.match(url.path)
    if match:
        return match["board"], match["job_id"]
    board, job_id = url.params.get("for"), url.params.get("token")
    if url.path.startswith("/embed/job_app") and board and job_id and job_id.isdigit():
        return board, job_id
    return None


class GreenhouseAdapter(SourceAdapter):
    name = "greenhouse"

    def __init__(self) -> None:
        # board -> the board's company name, or None when the board has none.
        self._board_names: dict[str, str | None] = {}

    def matches(self, url: httpx.URL) -> bool:
        return parse_greenhouse_url(url) is not None

    def job_key(self, url: httpx.URL) -> str:
        parsed = parse_greenhouse_url(url)
        assert parsed is not None
        return self.key_for(*parsed)

    @staticmethod
    def key_for(board: str, job_id: str) -> str:
        return f"greenhouse:{board.lower()}:{job_id}"

    def fetch(self, source_url: str, url: httpx.URL, ctx: FetchContext) -> JobFetchRecord:
        parsed = parse_greenhouse_url(url)
        assert parsed is not None
        return self.fetch_job(*parsed, source_url=source_url, ctx=ctx)

    def fetch_job(
        self, board: str, job_id: str, *, source_url: str, ctx: FetchContext
    ) -> JobFetchRecord:
        key = self.key_for(board, job_id)
        result = ctx.fetcher.get(
            _API.format(board=quote(board, safe=""), job_id=job_id), accept="application/json"
        )
        if result.status_code == 404:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.UNAVAILABLE,
                detail="Greenhouse: job not found (likely closed)",
                company=self._board_name(board, ctx), company_source=CompanySource.JOB_BOARD,
            )  # fmt: skip
        if not result.ok:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.FAILED,
                detail=f"Greenhouse API returned HTTP {result.status_code}",
            )  # fmt: skip
        data = result.json()
        content = text_or_none(data.get("content"))
        company, company_source = text_or_none(data.get("company_name")), CompanySource.JOB_POSTING
        if company is None:
            company, company_source = self._board_name(board, ctx), CompanySource.JOB_BOARD
        return self.record(
            ctx,
            source_url=source_url,
            job_key=key,
            status=FetchStatus.FETCHED,
            title=text_or_none(data.get("title")),
            company=company,
            company_source=company_source,
            # The API returns the description as HTML-escaped HTML.
            description_text=html_to_text(html.unescape(content)) if content else None,
            posting_url=text_or_none(data.get("absolute_url")),
        )

    def _board_name(self, board: str, ctx: FetchContext) -> str | None:
        """The board's company name from the Job Board API; it outlives closed jobs."""
        cache_key = board.lower()
        if cache_key not in self._board_names:
            try:
                result = ctx.fetcher.get(
                    _BOARD_API.format(board=quote(board, safe="")), accept="application/json"
                )
                data = result.json() if result.ok else {}
            except (FetchError, ValueError):
                data = {}
            self._board_names[cache_key] = (
                text_or_none(data.get("name")) if isinstance(data, dict) else None
            )
        return self._board_names[cache_key]
