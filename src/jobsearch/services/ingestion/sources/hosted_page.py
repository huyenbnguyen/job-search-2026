"""Shared helpers for reading a job's own web page: robots.txt checks and JSON-LD details.

Some job-board APIs (Lever, Ashby) don't return the company name, but the job's
hosted page publishes it as schema.org `hiringOrganization`. `HostedPageReader`
reads it there, and the page fetch is best-effort: if it fails, the job record
keeps everything the API returned and only the company stays empty.
"""

import logging
from urllib.robotparser import RobotFileParser

import httpx

from jobsearch.services.ingestion.fetch import FetchError
from jobsearch.services.ingestion.html_utils import find_job_postings
from jobsearch.services.ingestion.sources.base import FetchContext, text_or_none

logger = logging.getLogger(__name__)


class RobotsPolicy:
    """robots.txt checks per origin, cached for the run. Disabled means always allowed."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._parsers: dict[str, RobotFileParser] = {}

    def allows(self, url: httpx.URL, ctx: FetchContext) -> bool:
        if not self._enabled:
            return True
        origin = f"{url.scheme}://{url.netloc.decode('ascii')}"
        parser = self._parsers.get(origin)
        if parser is None:
            parser = RobotFileParser()
            try:
                result = ctx.fetcher.get(f"{origin}/robots.txt", accept="text/plain")
            except FetchError:
                result = None
            if result is not None and result.ok:
                parser.parse(result.text.splitlines())
            elif result is not None and 400 <= result.status_code < 500:
                parser.parse([])  # RFC 9309: no robots.txt means no restrictions.
            else:
                # RFC 9309: an unreachable robots.txt means assume everything is disallowed.
                parser.parse(["User-agent: *", "Disallow: /"])
            self._parsers[origin] = parser
        return parser.can_fetch(ctx.user_agent, str(url))


class HostedPageReader:
    def __init__(self, robots: RobotsPolicy) -> None:
        self._robots = robots

    def html(self, page_url: str | None, ctx: FetchContext) -> str | None:
        """The page's HTML if robots.txt allows it and it loads, else None. Never raises."""
        if not page_url:
            return None
        try:
            url = httpx.URL(page_url)
            if not self._robots.allows(url, ctx):
                return None
            result = ctx.fetcher.get(str(url), accept="text/html,application/xhtml+xml")
        except (FetchError, httpx.InvalidURL) as exc:
            logger.info(
                "hosted page unavailable",
                extra={"error_category": getattr(exc, "category", "invalid_url")},
            )
            return None
        if not result.ok or "html" not in result.content_type.lower():
            return None
        return result.text

    def company(self, page_url: str | None, ctx: FetchContext) -> str | None:
        """The hiring organization's name from the page's JobPosting data, or None."""
        page = self.html(page_url, ctx)
        for posting in find_job_postings(page or ""):
            if name := organization_name(posting.get("hiringOrganization")):
                return name
        return None


def organization_name(value: object) -> str | None:
    """schema.org hiringOrganization is either an Organization object or a plain name."""
    if isinstance(value, dict):
        return text_or_none(value.get("name"))
    return text_or_none(value)
