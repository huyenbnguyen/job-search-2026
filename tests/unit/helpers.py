"""Test helpers: a fake network with no real DNS or HTTP. All data is synthetic."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from jobsearch.config import FetchConfig
from jobsearch.services.ingestion.fetch import SafeFetcher
from jobsearch.services.ingestion.sources import FetchContext

PUBLIC_IP = "93.184.216.34"
FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
TEST_USER_AGENT = "jobsearch-test/0.1"

Handler = Callable[[httpx.Request], httpx.Response]


def public_resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def json_response(data: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code, content=json.dumps(data), headers={"content-type": "application/json"}
    )


def html_response(body: str, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code, content=body, headers={"content-type": "text/html; charset=utf-8"}
    )


class FakeNetwork:
    """Routes requests by exact URL (query string ignored); records every request."""

    def __init__(self, routes: dict[str, httpx.Response | Handler]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = str(request.url.copy_with(query=None))
        route = self.routes.get(key)
        if route is None:
            return streamed(httpx.Response(404))
        return streamed(route(request) if callable(route) else route)

    def requested(self, url: str) -> int:
        return sum(1 for r in self.requests if str(r.url.copy_with(query=None)) == url)


def streamed(response: httpx.Response) -> httpx.Response:
    """A fresh, unread copy of `response`, as a real transport returns it.

    The fetcher reads the raw stream, and httpx pre-reads a Response built from bytes.
    The body is copied decoded, so build encoded test responses with `stream=` instead.
    """
    return httpx.Response(
        response.status_code, headers=response.headers, stream=httpx.ByteStream(response.content)
    )


def make_fetcher(
    network: Handler,
    *,
    resolver: Callable[[str, int], list[str]] = public_resolver,
    sleeps: list[float] | None = None,
    **config: Any,
) -> SafeFetcher:
    settings = {"per_host_delay_seconds": 0.0, "backoff_base_seconds": 0.0, **config}
    recorded = sleeps if sleeps is not None else []
    return SafeFetcher(
        FetchConfig(**settings),
        transport=httpx.MockTransport(network),
        resolver=resolver,
        sleep=recorded.append,
    )


def make_context(fetcher: SafeFetcher) -> FetchContext:
    return FetchContext(
        fetcher=fetcher, run_id="test-run", user_agent=TEST_USER_AGENT, now=lambda: FIXED_NOW
    )
