"""SSRF-safe HTTP fetching for job pages and job-board APIs.

Every request:
- allows only http and https;
- resolves the host and rejects any address that isn't globally routable
  (private, loopback, link-local, reserved, multicast, ...);
- follows redirects by hand, re-checking each hop;
- has a timeout and a maximum response size;
- waits between requests to the same host;
- retries transient failures (network errors, HTTP 429 and 5xx) with
  exponential backoff and jitter.

Known limitation: the address check and httpx's own connection resolve DNS
separately, so a host that changes its DNS answer between the two (DNS rebinding)
isn't caught. Acceptable for a local tool fetching URLs the user chose.
"""

import ipaddress
import json
import random
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Self

import httpx

from jobsearch.config import FetchConfig

Resolver = Callable[[str, int], list[str]]

_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    """A fetch that produced no usable HTTP response. `category` is safe to log."""

    category = "fetch_error"


class BlockedURLError(FetchError):
    category = "blocked_url"


class ResponseTooLargeError(FetchError):
    category = "response_too_large"


class TooManyRedirectsError(FetchError):
    category = "too_many_redirects"


class NetworkError(FetchError):
    category = "network_error"


@dataclass(frozen=True)
class FetchResult:
    url: str
    status_code: int
    content_type: str
    body: bytes = field(repr=False)
    encoding: str = "utf-8"

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        return self.body.decode(self.encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.body)


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


def check_url_allowed(url: str, resolver: Resolver = default_resolver) -> httpx.URL:
    """Raise BlockedURLError unless `url` is http(s) and every address of its host is public."""
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise BlockedURLError("invalid URL") from exc
    if parsed.scheme not in ("http", "https"):
        raise BlockedURLError(f"scheme not allowed: {parsed.scheme or '(none)'}")
    if not parsed.host:
        raise BlockedURLError("URL has no host")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = resolver(parsed.host, port)
    except (OSError, UnicodeError) as exc:
        raise NetworkError(f"could not resolve host {parsed.host}") from exc
    if not addresses:
        raise NetworkError(f"could not resolve host {parsed.host}")
    for address in addresses:
        if not _is_public(address):
            raise BlockedURLError(f"host {parsed.host} resolves to a non-public address")
    return parsed


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class SafeFetcher:
    def __init__(
        self,
        config: FetchConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = default_resolver,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._resolver = resolver
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: dict[str, float] = {}
        self._client = httpx.Client(
            timeout=config.timeout_seconds,
            follow_redirects=False,
            headers={"User-Agent": config.user_agent},
            transport=transport,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, url: str, *, accept: str = "*/*") -> FetchResult:
        """GET `url`. Returns the final response for any HTTP status, else raises FetchError."""
        attempt = 0
        while True:
            try:
                result = self._get_following_redirects(url, accept)
            except NetworkError:
                if attempt >= self._config.max_retries:
                    raise
                self._sleep(self._backoff(attempt))
            else:
                if result.status_code not in _TRANSIENT_STATUS or (
                    attempt >= self._config.max_retries
                ):
                    return result
                self._sleep(self._backoff(attempt))
            attempt += 1

    def _get_following_redirects(self, url: str, accept: str) -> FetchResult:
        current = url
        for _ in range(self._config.max_redirects + 1):
            parsed = check_url_allowed(current, self._resolver)
            self._throttle(parsed.host)
            try:
                with self._client.stream("GET", parsed, headers={"Accept": accept}) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("redirect without a Location header")
                        current = str(parsed.join(location))
                        continue
                    body = self._read_limited(response)
                    return FetchResult(
                        url=current,
                        status_code=response.status_code,
                        content_type=response.headers.get("content-type", ""),
                        body=body,
                        encoding=response.encoding or "utf-8",
                    )
            except httpx.TransportError as exc:
                raise NetworkError(f"{type(exc).__name__} for host {parsed.host}") from exc
        raise TooManyRedirectsError(f"more than {self._config.max_redirects} redirects")

    def _read_limited(self, response: httpx.Response) -> bytes:
        limit = self._config.max_response_bytes
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise ResponseTooLargeError(f"response larger than {limit} bytes")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > limit:
                raise ResponseTooLargeError(f"response larger than {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def _throttle(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self._config.per_host_delay_seconds - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at[host] = self._clock()

    def _backoff(self, attempt: int) -> float:
        base = self._config.backoff_base_seconds
        return float(base * 2**attempt + random.uniform(0, base))
