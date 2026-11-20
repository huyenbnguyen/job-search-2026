import gzip
import zlib
from collections.abc import Iterator

import httpx
import pytest

from jobsearch.config import FetchConfig
from jobsearch.services.ingestion.fetch import (
    BlockedURLError,
    DeadlineExceededError,
    FetchError,
    NetworkError,
    NotPermittedError,
    ResponseTooLargeError,
    SafeFetcher,
    TooManyRedirectsError,
    _CappedDecoder,
    check_url_allowed,
)
from tests.unit.helpers import PUBLIC_IP, FakeNetwork, make_fetcher, public_resolver


def resolver_for(mapping: dict[str, str]):  # type: ignore[no-untyped-def]
    def resolve(host: str, port: int) -> list[str]:
        return [mapping.get(host, PUBLIC_IP)]

    return resolve


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com", "//example.com/x"],
)
def test_rejects_non_http_schemes(url: str) -> None:
    with pytest.raises(BlockedURLError):
        check_url_allowed(url, public_resolver)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.16.0.5",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata endpoint
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "224.0.0.1",
    ],
)
def test_rejects_non_public_addresses(address: str) -> None:
    with pytest.raises(BlockedURLError):
        check_url_allowed("https://jobs.example.com/1", lambda host, port: [address])


def test_rejects_when_any_resolved_address_is_private() -> None:
    with pytest.raises(BlockedURLError):
        check_url_allowed("https://jobs.example.com/", lambda host, port: [PUBLIC_IP, "10.0.0.1"])


def test_unresolvable_host_is_a_network_error() -> None:
    def fail(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    with pytest.raises(NetworkError):
        check_url_allowed("https://missing.example.com/", fail)


def test_allows_public_https_url() -> None:
    assert (
        check_url_allowed("https://jobs.example.com/1", public_resolver).host == "jobs.example.com"
    )


def test_redirect_to_private_address_is_blocked() -> None:
    network = FakeNetwork(
        {
            "https://jobs.example.com/1": httpx.Response(
                302, headers={"location": "http://internal.example.com/admin"}
            ),
            "http://internal.example.com/admin": httpx.Response(200, content=b"secret"),
        }
    )
    resolver = resolver_for({"internal.example.com": "10.0.0.7"})
    with make_fetcher(network, resolver=resolver) as fetcher, pytest.raises(BlockedURLError):
        fetcher.get("https://jobs.example.com/1")
    assert network.requested("http://internal.example.com/admin") == 0


def test_follows_relative_redirects() -> None:
    network = FakeNetwork(
        {
            "https://jobs.example.com/old": httpx.Response(301, headers={"location": "/new"}),
            "https://jobs.example.com/new": httpx.Response(200, content=b"ok"),
        }
    )
    with make_fetcher(network) as fetcher:
        result = fetcher.get("https://jobs.example.com/old")
    assert (result.status_code, result.url, result.text) == (
        200,
        "https://jobs.example.com/new",
        "ok",
    )


def test_redirect_limit() -> None:
    network = FakeNetwork(
        {"https://jobs.example.com/loop": httpx.Response(302, headers={"location": "/loop"})}
    )
    with make_fetcher(network, max_redirects=2) as fetcher, pytest.raises(TooManyRedirectsError):
        fetcher.get("https://jobs.example.com/loop")
    assert network.requested("https://jobs.example.com/loop") == 3


def test_response_size_limit() -> None:
    network = FakeNetwork({"https://jobs.example.com/big": httpx.Response(200, content=b"x" * 101)})
    with (
        make_fetcher(network, max_response_bytes=100) as fetcher,
        pytest.raises(ResponseTooLargeError),
    ):
        fetcher.get("https://jobs.example.com/big")


def test_retries_transient_status_then_succeeds() -> None:
    responses = iter([httpx.Response(503), httpx.Response(429), httpx.Response(200, content=b"ok")])
    network = FakeNetwork({"https://jobs.example.com/1": lambda request: next(responses)})
    with make_fetcher(network, max_retries=3) as fetcher:
        assert fetcher.get("https://jobs.example.com/1").status_code == 200
    assert network.requested("https://jobs.example.com/1") == 3


def test_returns_last_transient_response_after_retries_run_out() -> None:
    network = FakeNetwork({"https://jobs.example.com/1": httpx.Response(503)})
    with make_fetcher(network, max_retries=2) as fetcher:
        assert fetcher.get("https://jobs.example.com/1").status_code == 503
    assert network.requested("https://jobs.example.com/1") == 3


def test_does_not_retry_permanent_status() -> None:
    network = FakeNetwork({"https://jobs.example.com/1": httpx.Response(404)})
    with make_fetcher(network, max_retries=3) as fetcher:
        assert fetcher.get("https://jobs.example.com/1").status_code == 404
    assert network.requested("https://jobs.example.com/1") == 1


def test_retries_network_errors_then_raises() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with make_fetcher(refuse, max_retries=2) as fetcher, pytest.raises(NetworkError):
        fetcher.get("https://jobs.example.com/1")


def test_waits_between_requests_to_the_same_host() -> None:
    network = FakeNetwork(
        {
            "https://a.example.com/1": httpx.Response(200),
            "https://a.example.com/2": httpx.Response(200),
            "https://b.example.com/1": httpx.Response(200),
        }
    )
    sleeps: list[float] = []
    with make_fetcher(network, sleeps=sleeps, per_host_delay_seconds=5.0) as fetcher:
        fetcher.get("https://a.example.com/1")
        fetcher.get("https://b.example.com/1")
        assert sleeps == []
        fetcher.get("https://a.example.com/2")
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 5.0


def test_sends_configured_user_agent() -> None:
    network = FakeNetwork({"https://jobs.example.com/1": httpx.Response(200)})
    with make_fetcher(network, user_agent="custom-agent/1.0") as fetcher:
        fetcher.get("https://jobs.example.com/1")
    assert network.requests[0].headers["user-agent"] == "custom-agent/1.0"


def encoded(body: bytes, encoding: str) -> httpx.Response:
    # Built with stream= so the test fake passes the encoded bytes through unchanged.
    return httpx.Response(
        200, headers={"content-encoding": encoding}, stream=httpx.ByteStream(body)
    )


def test_requests_only_encodings_it_can_cap() -> None:
    network = FakeNetwork({"https://jobs.example.com/1": httpx.Response(200)})
    with make_fetcher(network) as fetcher:
        fetcher.get("https://jobs.example.com/1")
    assert network.requests[0].headers["accept-encoding"] == "gzip, deflate"


@pytest.mark.parametrize(
    "body_and_encoding",
    [
        (gzip.compress(b"<p>job</p>" * 5), "gzip"),
        (zlib.compress(b"<p>job</p>" * 5), "deflate"),
        (zlib.compress(b"<p>job</p>" * 5)[2:-4], "deflate"),  # raw deflate, no zlib header
    ],
    ids=["gzip", "deflate", "raw_deflate"],
)
def test_decodes_compressed_responses_within_the_limit(
    body_and_encoding: tuple[bytes, str],
) -> None:
    body, encoding = body_and_encoding
    with make_fetcher(lambda request: encoded(body, encoding), max_response_bytes=50) as fetcher:
        assert fetcher.get("https://jobs.example.com/1").body == b"<p>job</p>" * 5


def test_compressed_response_over_the_limit_is_rejected() -> None:
    bomb = gzip.compress(b"\0" * 10_000_000)  # about 10 KB on the wire
    with (
        make_fetcher(lambda request: encoded(bomb, "gzip"), max_response_bytes=1000) as fetcher,
        pytest.raises(ResponseTooLargeError),
    ):
        fetcher.get("https://jobs.example.com/1")


def test_decoder_never_returns_more_than_asked() -> None:
    decoder = _CappedDecoder("gzip")
    assert len(decoder.decode(gzip.compress(b"\0" * 1_000_000), 101)) == 101


def test_unsupported_content_encoding_is_an_error() -> None:
    with (
        make_fetcher(lambda request: encoded(b"x", "br")) as fetcher,
        pytest.raises(FetchError, match="unsupported content-encoding"),
    ):
        fetcher.get("https://jobs.example.com/1")


def test_slow_body_hits_the_total_deadline() -> None:
    now = [0.0]

    def clock() -> float:
        return now[0]

    def drip() -> Iterator[bytes]:
        for _ in range(100):
            now[0] += 1.0  # each chunk arrives within httpx's inactivity timeout
            yield b"x"

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_Iter(drip()))

    fetcher = SafeFetcher(
        FetchConfig(timeout_seconds=10, max_retries=0, per_host_delay_seconds=0),
        transport=httpx.MockTransport(respond),
        resolver=public_resolver,
        sleep=lambda seconds: None,
        clock=clock,
    )
    with fetcher, pytest.raises(DeadlineExceededError):
        fetcher.get("https://jobs.example.com/slow")
    assert now[0] <= 11


class _Iter(httpx.SyncByteStream):
    def __init__(self, chunks: Iterator[bytes]) -> None:
        self._chunks = chunks

    def __iter__(self) -> Iterator[bytes]:
        yield from self._chunks


def test_redirect_to_linkedin_is_not_followed() -> None:
    network = FakeNetwork(
        {
            "https://jobs.example.com/1": httpx.Response(
                302, headers={"location": "https://www.linkedin.com/jobs/view/4000000001/"}
            ),
        }
    )
    with make_fetcher(network) as fetcher, pytest.raises(NotPermittedError):
        fetcher.get("https://jobs.example.com/1")
    assert [str(r.url) for r in network.requests] == ["https://jobs.example.com/1"]


def test_hop_check_runs_before_every_hop() -> None:
    network = FakeNetwork(
        {
            "https://a.example.com/1": httpx.Response(
                302, headers={"location": "https://b.example.com/2"}
            ),
            "https://b.example.com/2": httpx.Response(200, content=b"ok"),
        }
    )
    checked: list[str] = []

    def check(url: httpx.URL) -> None:
        checked.append(str(url))
        if url.host == "b.example.com":
            raise NotPermittedError("not this one")

    with make_fetcher(network) as fetcher, pytest.raises(NotPermittedError):
        fetcher.get("https://a.example.com/1", check_hop=check)
    assert checked == ["https://a.example.com/1", "https://b.example.com/2"]
    assert network.requested("https://b.example.com/2") == 0
