"""Lever, through its public Postings API (api.lever.co/v0/postings)."""

import re
from typing import Any
from urllib.parse import quote

import httpx

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.html_utils import html_to_text, page_title
from jobsearch.services.ingestion.sources.base import FetchContext, SourceAdapter, text_or_none
from jobsearch.services.ingestion.sources.hosted_page import HostedPageReader

_API_HOSTS = {"jobs.lever.co": "api.lever.co", "jobs.eu.lever.co": "api.eu.lever.co"}
_JOB_PATH = re.compile(r"^/(?P<company>[\w.-]+)/(?P<job_id>[0-9a-f-]{36})(?:/|$)", re.I)


def parse_lever_url(url: httpx.URL) -> tuple[str, str, str] | None:
    """Return (api_host, company, job_id) for a Lever job URL, or None."""
    api_host = _API_HOSTS.get(url.host)
    match = _JOB_PATH.match(url.path) if api_host else None
    if api_host is None or match is None:
        return None
    return api_host, match["company"], match["job_id"].lower()


class LeverAdapter(SourceAdapter):
    name = "lever"

    def __init__(self, pages: HostedPageReader) -> None:
        # The Postings API has no company name; the hosted posting page does.
        self._pages = pages
        # board page URL -> its <title>, which Lever sets to the company name.
        self._board_names: dict[str, str | None] = {}

    def matches(self, url: httpx.URL) -> bool:
        return parse_lever_url(url) is not None

    def job_key(self, url: httpx.URL) -> str:
        parsed = parse_lever_url(url)
        assert parsed is not None
        _, company, job_id = parsed
        return f"lever:{company.lower()}:{job_id}"

    def fetch(self, source_url: str, url: httpx.URL, ctx: FetchContext) -> JobFetchRecord:
        parsed = parse_lever_url(url)
        assert parsed is not None
        api_host, board, job_id = parsed
        key = self.job_key(url)
        result = ctx.fetcher.get(
            f"https://{api_host}/v0/postings/{quote(board, safe='')}/{job_id}",
            accept="application/json",
        )
        board_page = f"https://{url.host}/{quote(board, safe='')}"
        if result.status_code == 404:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.UNAVAILABLE,
                detail="Lever: posting not found (likely closed)",
                company=self._board_name(board_page, ctx), company_source=CompanySource.JOB_BOARD,
            )  # fmt: skip
        if not result.ok:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.FAILED,
                detail=f"Lever API returned HTTP {result.status_code}",
            )  # fmt: skip
        data = result.json()
        posting_url = text_or_none(data.get("hostedUrl"))
        company, company_source = self._pages.company(posting_url, ctx), CompanySource.JOB_POSTING
        if company is None:
            company, company_source = self._board_name(board_page, ctx), CompanySource.JOB_BOARD
        return self.record(
            ctx,
            source_url=source_url,
            job_key=key,
            status=FetchStatus.FETCHED,
            title=text_or_none(data.get("text")),
            company=company,
            company_source=company_source,
            description_text=_description(data),
            posting_url=posting_url,
        )

    def _board_name(self, board_page: str, ctx: FetchContext) -> str | None:
        if board_page not in self._board_names:
            page = self._pages.html(board_page, ctx)
            self._board_names[board_page] = page_title(page) if page else None
        return self._board_names[board_page]


def _description(data: dict[str, Any]) -> str | None:
    """Lever splits a posting into an intro, titled lists, and closing text."""
    sections: list[str] = []
    if intro := text_or_none(data.get("descriptionPlain")):
        sections.append(intro)
    for item in data.get("lists") or []:
        if not isinstance(item, dict):
            continue
        heading = text_or_none(item.get("text"))
        body = html_to_text(item["content"]) if text_or_none(item.get("content")) else ""
        section = "\n".join(part for part in (heading, body) if part)
        if section:
            sections.append(section)
    if closing := text_or_none(data.get("additionalPlain")):
        sections.append(closing)
    return "\n\n".join(sections) or None
