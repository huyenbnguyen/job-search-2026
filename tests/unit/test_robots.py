import httpx
import pytest

from jobsearch.services.ingestion.robots import RobotsRules

AGENT = "jobsearch-test/0.1 (synthetic)"


def allowed(robots: str, path: str, agent: str = AGENT) -> bool:
    return RobotsRules.parse(robots).allows(agent, httpx.URL(f"https://example.com{path}"))


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/jobs/42", True),
        ("/jobs/42?print=1", False),  # Disallow: /*?print=
        ("/files/report.pdf", False),  # Disallow: /*.pdf$
        ("/files/report.pdf?x=1", True),  # "$" anchors the end, so a query escapes it
        ("/private/a", False),  # a literal prefix
        ("/private-notes", False),  # prefixes don't stop at "/"
        ("/privat", True),
    ],
)
def test_wildcards_anchors_and_prefixes(path: str, expected: bool) -> None:
    robots = "User-agent: *\nDisallow: /*?print=\nDisallow: /*.pdf$\nDisallow: /private\n"
    assert allowed(robots, path) is expected


def test_longest_match_wins_and_allow_wins_ties() -> None:
    robots = (
        "User-agent: *\n"
        "Disallow: /jobs/\n"
        "Allow: /jobs/open/\n"  # longer than Disallow: /jobs/
        "Disallow: /team\n"
        "Allow: /team\n"  # same length: allow wins
    )
    assert allowed(robots, "/jobs/closed/1") is False
    assert allowed(robots, "/jobs/open/1") is True
    assert allowed(robots, "/team") is True


def test_wildcard_length_counts_toward_longest_match() -> None:
    robots = "User-agent: *\nAllow: /jobs/*/apply\nDisallow: /jobs/\n"
    assert allowed(robots, "/jobs/42/apply") is True
    assert allowed(robots, "/jobs/42") is False


def test_product_token_group_beats_the_star_group() -> None:
    robots = "User-agent: *\nDisallow: /\n\nUser-agent: JobSearch-Test\nDisallow: /admin\n"
    assert allowed(robots, "/jobs/42") is True
    assert allowed(robots, "/admin") is False
    assert allowed(robots, "/jobs/42", agent="other-bot/1.0") is False


def test_groups_for_the_same_agent_are_combined() -> None:
    robots = "User-agent: a\nUser-agent: *\nDisallow: /x\n\nUser-agent: *\nDisallow: /y\n"
    assert allowed(robots, "/x") is False
    assert allowed(robots, "/y") is False
    assert allowed(robots, "/z") is True


def test_empty_disallow_and_no_rules_allow_everything() -> None:
    assert allowed("User-agent: *\nDisallow:\n", "/anything") is True
    assert allowed("", "/anything") is True
    assert RobotsRules.allow_all().allows(AGENT, httpx.URL("https://example.com/a")) is True


def test_disallow_all_still_allows_robots_txt() -> None:
    rules = RobotsRules.disallow_all()
    assert rules.allows(AGENT, httpx.URL("https://example.com/jobs/1")) is False
    assert rules.allows(AGENT, httpx.URL("https://example.com/robots.txt")) is True


def test_comments_and_percent_encoding() -> None:
    robots = "User-agent: *  # everyone\nDisallow: /caf%c3%a9 # cafe page\nDisallow: /naïve\n"
    assert allowed(robots, "/caf%C3%A9/menu") is False
    assert allowed(robots, "/na%C3%AFve") is False
    assert allowed(robots, "/cafe") is True
