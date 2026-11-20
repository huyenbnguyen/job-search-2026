"""Company career pages: one polite GET per URL, read for machine-readable job data.

In order, the adapter:
1. skips the page if the site's robots.txt disallows it (configurable);
2. when the URL carries a `gh_jid` or `ashby_jid` and the page names its Greenhouse
   board or Ashby organization, uses that job board's public API for that exact job;
3. otherwise reads a schema.org JobPosting embedded as JSON-LD: the only complete one
   on the page, or among several, the one whose `url` is this page;
4. otherwise, when the URL carries a `gh_jid`, tries the company name from the URL as
   the board. That's a guess, so only a successful lookup counts: a miss says nothing
   about whether the job is closed.

Many career sites render jobs with JavaScript only; those end as `no_details`
and are left for the paste-text fallback.
"""

import re
from typing import Any

import httpx

from jobsearch.models.job import FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.fetch import NotPermittedError
from jobsearch.services.ingestion.html_utils import find_job_postings, html_to_text
from jobsearch.services.ingestion.sources.ashby import AshbyAdapter
from jobsearch.services.ingestion.sources.base import (
    FetchContext,
    SourceAdapter,
    normalize_url,
    text_or_none,
)
from jobsearch.services.ingestion.sources.company_from_url import company_from_url
from jobsearch.services.ingestion.sources.greenhouse import GreenhouseAdapter
from jobsearch.services.ingestion.sources.hosted_page import RobotsPolicy, organization_name

_GREENHOUSE_BOARD = (
    re.compile(r"greenhouse\.io/embed/job_(?:board|app)[^\"'\s<>]*?[?&;]for=([\w-]+)"),
    re.compile(r"boards-api\.greenhouse\.io/v1/boards/([\w-]+)"),
    re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed\b)([\w-]+)"),
)
_ASHBY_ORG = (
    re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([\w.-]+)"),
    re.compile(r"jobs\.ashbyhq\.com/(?!api\b)([\w.-]+)"),
)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class CareerPageAdapter(SourceAdapter):
    name = "career_page"

    def __init__(
        self,
        greenhouse: GreenhouseAdapter,
        ashby: AshbyAdapter,
        robots: RobotsPolicy,
    ) -> None:
        self._greenhouse = greenhouse
        self._ashby = ashby
        self._robots = robots

    def matches(self, url: httpx.URL) -> bool:
        return url.scheme in ("http", "https")

    def job_key(self, url: httpx.URL) -> str:
        return f"page:{normalize_url(url)}"

    def fetch(self, source_url: str, url: httpx.URL, ctx: FetchContext) -> JobFetchRecord:
        key = self.job_key(url)
        try:
            # robots.txt is checked for the page and for every redirect it follows.
            result = self._robots.get_page(str(url), ctx)
        except NotPermittedError as exc:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.NOT_PERMITTED,
                detail=str(exc),
            )  # fmt: skip
        if result.status_code in (404, 410):
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.UNAVAILABLE,
                detail=f"page returned HTTP {result.status_code} (likely removed)",
            )  # fmt: skip
        if not result.ok:
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.FAILED,
                detail=f"page returned HTTP {result.status_code}",
            )  # fmt: skip
        if "html" not in result.content_type.lower():
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.NO_DETAILS,
                detail="response is not an HTML page",
            )  # fmt: skip

        page = result.text
        # A board job ID names the exact job, so ask that board first: the page's JSON-LD
        # may describe a different job, for example on a page listing several.
        gh_jid = url.params.get("gh_jid", "")
        if gh_jid.isdigit() and (board := _first_match(_GREENHOUSE_BOARD, page)):
            return self._greenhouse.fetch_job(board, gh_jid, source_url=source_url, ctx=ctx)
        ashby_jid = url.params.get("ashby_jid", "")
        if _UUID.match(ashby_jid) and (org := _first_match(_ASHBY_ORG, page)):
            return self._ashby.fetch_job(org, ashby_jid, source_url=source_url, ctx=ctx)

        if posting := _requested_posting(find_job_postings(page), url, result.url):
            return self.record(
                ctx,
                source_url=source_url,
                job_key=key,
                status=FetchStatus.FETCHED,
                title=text_or_none(posting.get("title")),
                company=organization_name(posting.get("hiringOrganization")),
                description_text=html_to_text(text_or_none(posting.get("description")) or ""),
                posting_url=text_or_none(posting.get("url")) or result.url,
            )

        if gh_jid.isdigit() and (guess := company_from_url(url)):
            found = self._greenhouse.fetch_job(guess, gh_jid, source_url=source_url, ctx=ctx)
            if found.status is FetchStatus.FETCHED:
                return found

        return self.record(
            ctx, source_url=source_url, job_key=key, status=FetchStatus.NO_DETAILS,
            detail="no machine-readable job data; the page may need JavaScript",
        )  # fmt: skip


def _requested_posting(
    postings: list[dict[str, Any]], url: httpx.URL, final_url: str
) -> dict[str, Any] | None:
    """The page's complete JobPosting for the requested job, or None.

    A single complete posting is taken as the page's job. With several, only one whose
    `url` is the requested or final page counts; otherwise the page is a listing.
    """
    complete = [
        p for p in postings if text_or_none(p.get("title")) and text_or_none(p.get("description"))
    ]
    if len(complete) == 1:
        return complete[0]
    wanted = {normalize_url(url), normalize_url(httpx.URL(final_url))}
    for posting in complete:
        posting_url = text_or_none(posting.get("url"))
        try:
            if posting_url and normalize_url(httpx.URL(posting_url)) in wanted:
                return posting
        except httpx.InvalidURL:
            continue
    return None


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        if match := pattern.search(text):
            return match[1]
    return None
