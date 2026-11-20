"""Shared helpers for reading a job's own web page: robots.txt checks and JSON-LD details.

Some job-board APIs (Lever, Ashby) don't return the company name, but the job's
hosted page publishes it as schema.org `hiringOrganization`. `HostedPageReader`
reads it there, and the page fetch is best-effort: if it fails, the job record
keeps everything the API returned and only the company stays empty.
"""

import logging

import httpx

from jobsearch.services.ingestion.fetch import FetchError, FetchResult, NotPermittedError
from jobsearch.services.ingestion.html_utils import find_job_postings
from jobsearch.services.ingestion.robots import RobotsRules
from jobsearch.services.ingestion.sources.base import FetchContext, text_or_none

logger = logging.getLogger(__name__)


class RobotsPolicy:
    """robots.txt checks per origin, cached for the run. Disabled means always allowed."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = enabled
        self._rules: dict[str, RobotsRules] = {}

    def allows(self, url: httpx.URL, ctx: FetchContext) -> bool:
        if not self._enabled:
            return True
        origin = f"{url.scheme}://{url.netloc.decode('ascii')}"
        rules = self._rules.get(origin)
        if rules is None:
            try:
                result = ctx.fetcher.get(f"{origin}/robots.txt", accept="text/plain")
            except FetchError:
                result = None
            if result is not None and result.ok:
                rules = RobotsRules.parse(result.text)
            elif result is not None and 400 <= result.status_code < 500:
                rules = RobotsRules.allow_all()  # RFC 9309: no robots.txt, no restrictions.
            else:
                # RFC 9309: an unreachable robots.txt means assume everything is disallowed.
                rules = RobotsRules.disallow_all()
            self._rules[origin] = rules
        return rules.allows(ctx.user_agent, url)

    def require(self, url: httpx.URL, ctx: FetchContext) -> None:
        """Raise NotPermittedError unless robots.txt allows `url`. A SafeFetcher hop check."""
        if not self.allows(url, ctx):
            raise NotPermittedError("robots.txt disallows automated access to this page")

    def get_page(self, url: str, ctx: FetchContext) -> FetchResult:
        """GET an HTML page, checking robots.txt for it and for every redirect it follows."""
        return ctx.fetcher.get(
            url,
            accept="text/html,application/xhtml+xml",
            check_hop=lambda hop: self.require(hop, ctx),
        )


class HostedPageReader:
    def __init__(self, robots: RobotsPolicy) -> None:
        self._robots = robots

    def html(self, page_url: str | None, ctx: FetchContext) -> str | None:
        """The page's HTML if robots.txt allows it and it loads, else None. Never raises."""
        if not page_url:
            return None
        try:
            result = self._robots.get_page(str(httpx.URL(page_url)), ctx)
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
