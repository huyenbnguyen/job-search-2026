"""Company career pages: one polite GET per URL, read for machine-readable job data.

In order, the adapter:
1. skips the page if the site's robots.txt disallows it (configurable);
2. reads a schema.org JobPosting embedded as JSON-LD;
3. otherwise, when the URL carries a `gh_jid` or `ashby_jid` and the page names its
   Greenhouse board or Ashby organization, uses that job board's public API;
4. otherwise, when the URL carries a `gh_jid`, tries the company name from the URL as
   the board. That's a guess, so only a successful lookup counts: a miss says nothing
   about whether the job is closed.

Many career sites render jobs with JavaScript only; those end as `no_details`
and are left for the paste-text fallback.
"""

import re

import httpx

from jobsearch.models.job import FetchStatus, JobFetchRecord
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
        if not self._robots.allows(url, ctx):
            return self.record(
                ctx, source_url=source_url, job_key=key, status=FetchStatus.NOT_PERMITTED,
                detail="robots.txt disallows automated access to this page",
            )  # fmt: skip

        result = ctx.fetcher.get(str(url), accept="text/html,application/xhtml+xml")
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
        for posting in find_job_postings(page):
            title = text_or_none(posting.get("title"))
            description = text_or_none(posting.get("description"))
            if title and description:
                return self.record(
                    ctx,
                    source_url=source_url,
                    job_key=key,
                    status=FetchStatus.FETCHED,
                    title=title,
                    company=organization_name(posting.get("hiringOrganization")),
                    description_text=html_to_text(description),
                    posting_url=text_or_none(posting.get("url")) or result.url,
                )

        gh_jid = url.params.get("gh_jid", "")
        if gh_jid.isdigit() and (board := _first_match(_GREENHOUSE_BOARD, page)):
            return self._greenhouse.fetch_job(board, gh_jid, source_url=source_url, ctx=ctx)
        ashby_jid = url.params.get("ashby_jid", "")
        if _UUID.match(ashby_jid) and (org := _first_match(_ASHBY_ORG, page)):
            return self._ashby.fetch_job(org, ashby_jid, source_url=source_url, ctx=ctx)
        if gh_jid.isdigit() and (guess := company_from_url(url)):
            found = self._greenhouse.fetch_job(guess, gh_jid, source_url=source_url, ctx=ctx)
            if found.status is FetchStatus.FETCHED:
                return found

        return self.record(
            ctx, source_url=source_url, job_key=key, status=FetchStatus.NO_DETAILS,
            detail="no machine-readable job data; the page may need JavaScript",
        )  # fmt: skip


def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> str | None:
    for pattern in patterns:
        if match := pattern.search(text):
            return match[1]
    return None
