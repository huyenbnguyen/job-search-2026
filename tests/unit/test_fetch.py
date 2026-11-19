import httpx
import pytest

from jobsearch.services.ingestion.fetch import (
    BlockedURLError,
    NetworkError,
    ResponseTooLargeError,
    TooManyRedirectsError,
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
