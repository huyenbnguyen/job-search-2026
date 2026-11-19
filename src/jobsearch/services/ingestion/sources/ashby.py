"""Ashby, through its public Job Posting API (api.ashbyhq.com/posting-api).

The API lists a whole board, not single jobs, so each board is fetched once per run.
A job that is no longer on its board is treated as closed.
"""

import json
import re
from typing import Any
from urllib.parse import quote, unquote

import httpx

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.html_utils import html_to_text
from jobsearch.services.ingestion.sources.base import FetchContext, SourceAdapter, text_or_none
from jobsearch.services.ingestion.sources.hosted_page import HostedPageReader

_HOST = "jobs.ashbyhq.com"
_API = "https://api.ashbyhq.com/posting-api/job-board/{org}"
_BOARD_PAGE = "https://jobs.ashbyhq.com/{org}"
# The board page embeds its app data as JSON, including "organization": {"name": ...}.
_ORGANIZATION_KEY = '"organization":'
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def parse_ashby_url(url: httpx.URL) -> tuple[str, str] | None:
    """Return (org, job_id) for an Ashby job URL, or None."""
    if url.host != _HOST:
        return None
    parts = [unquote(part) for part in url.path.split("/") if part]
    if len(parts) >= 2 and _UUID.match(parts[1]):
        return parts[0], parts[1].lower()
    return None


class AshbyAdapter(SourceAdapter):
    name = "ashby"

    def __init__(self, pages: HostedPageReader) -> None:
        # The Posting API has no company name; the hosted job page does.
        self._pages = pages
        self._board_names: dict[str, str | None] = {}
        # org -> jobs by id, or the HTTP status when the board itself could not be loaded.
        self._boards: dict[str, dict[str, dict[str, Any]] | int] = {}

    def matches(self, url: httpx.URL) -> bool:
        return parse_ashby_url(url) is not None

    def job_key(self, url: httpx.URL) -> str:
        parsed = parse_ashby_url(url)
        assert parsed is not None
        return self.key_for(*parsed)

    @staticmethod
    def key_for(org: str, job_id: str) -> str:
        return f"ashby:{org.lower()}:{job_id.lower()}"

    def fetch(self, source_url: str, url: httpx.URL, ctx: FetchContext) -> JobFetchRecord:
        parsed = parse_ashby_url(url)
        assert parsed is not None
        return self.fetch_job(*parsed, source_url=source_url, ctx=ctx)

    def fetch_job(
        self, org: str, job_id: str, *, source_url: str, ctx: FetchContext
    ) -> JobFetchRecord:
        key = self.key_for(org, job_id)
        board = self._load_board(org, ctx)
        if isinstance(board, int):
            status = FetchStatus.UNAVAILABLE if board == 404 else FetchStatus.FAILED
            return self.record(
                ctx, source_url=source_url, job_key=key, status=status,
                detail=f"Ashby board API returned HTTP {board}",
                company=self._board_name(org, ctx), company_source=CompanySource.JOB_BOARD,
            )  # fmt: skip
        job = board.get(job_id.lower())
        if job is None:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.UNAVAILABLE,
                detail="Ashby: job no longer listed on the board (likely closed)",
                company=self._board_name(org, ctx), company_source=CompanySource.JOB_BOARD,
            )  # fmt: skip
        description = text_or_none(job.get("descriptionPlain"))
        if description is None and (html := text_or_none(job.get("descriptionHtml"))):
            description = html_to_text(html)
        posting_url = text_or_none(job.get("jobUrl"))
        company, company_source = self._pages.company(posting_url, ctx), CompanySource.JOB_POSTING
        if company is None:
            company, company_source = self._board_name(org, ctx), CompanySource.JOB_BOARD
        return self.record(
            ctx,
            source_url=source_url,
            job_key=key,
            status=FetchStatus.FETCHED,
            title=text_or_none(job.get("title")),
            company=company,
            company_source=company_source,
            description_text=description,
            posting_url=posting_url,
        )

    def _load_board(self, org: str, ctx: FetchContext) -> dict[str, dict[str, Any]] | int:
        cache_key = org.lower()
        if cache_key not in self._boards:
            result = ctx.fetcher.get(
                _API.format(org=quote(org, safe="")), accept="application/json"
            )
            if result.ok:
                jobs = result.json().get("jobs", [])
                self._boards[cache_key] = {
                    str(job["id"]).lower(): job
                    for job in jobs
                    if isinstance(job, dict) and "id" in job
                }
            else:
                self._boards[cache_key] = result.status_code
        return self._boards[cache_key]

    def _board_name(self, org: str, ctx: FetchContext) -> str | None:
        """The organization name embedded in the Ashby board page, or None."""
        cache_key = org.lower()
        if cache_key not in self._board_names:
            page = self._pages.html(_BOARD_PAGE.format(org=quote(org, safe="")), ctx)
            name = _organization_name(page or "")
            self._board_names[cache_key] = name
        return self._board_names[cache_key]


def _organization_name(page: str) -> str | None:
    decoder = json.JSONDecoder()
    start = page.find(_ORGANIZATION_KEY)
    while start != -1:
        value_start = start + len(_ORGANIZATION_KEY)
        while value_start < len(page) and page[value_start].isspace():
            value_start += 1
        try:
            value, _ = decoder.raw_decode(page, value_start)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict) and (name := text_or_none(value.get("name"))):
            return name
        start = page.find(_ORGANIZATION_KEY, start + 1)
    return None
