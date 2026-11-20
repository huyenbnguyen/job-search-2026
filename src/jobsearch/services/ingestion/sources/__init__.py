"""Job source adapters. `default_adapters` lists them in matching order."""

from jobsearch.services.ingestion.sources.ashby import AshbyAdapter
from jobsearch.services.ingestion.sources.base import FetchContext, SourceAdapter
from jobsearch.services.ingestion.sources.career_page import CareerPageAdapter
from jobsearch.services.ingestion.sources.greenhouse import GreenhouseAdapter
from jobsearch.services.ingestion.sources.hosted_page import HostedPageReader, RobotsPolicy
from jobsearch.services.ingestion.sources.lever import LeverAdapter
from jobsearch.services.ingestion.sources.linkedin import LinkedInAdapter


def default_adapters(*, respect_robots_txt: bool = True) -> list[SourceAdapter]:
    """Specific sources first; the career-page adapter matches any http(s) URL, so it's last."""
    robots = RobotsPolicy(enabled=respect_robots_txt)
    pages = HostedPageReader(robots)
    greenhouse = GreenhouseAdapter()
    ashby = AshbyAdapter(pages)
    return [
        LinkedInAdapter(),
        greenhouse,
        ashby,
        LeverAdapter(pages),
        CareerPageAdapter(greenhouse, ashby, robots),
    ]


__all__ = ["FetchContext", "SourceAdapter", "default_adapters"]
