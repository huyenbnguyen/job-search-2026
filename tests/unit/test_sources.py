import json

import httpx
import pytest

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.sources import SourceAdapter, default_adapters
from jobsearch.services.ingestion.sources.base import normalize_url
from tests.unit.helpers import (
    FakeNetwork,
    html_response,
    json_response,
    make_context,
    make_fetcher,
)

GH_API = "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs/1234567"
ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/exampleorg"
ASHBY_ID = "11111111-2222-3333-4444-555555555555"
LEVER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
LEVER_API = f"https://api.lever.co/v0/postings/examplelever/{LEVER_ID}"
ROBOTS_ALLOW = httpx.Response(200, content=b"User-agent: *\nAllow: /\n")


def adapter_for(url: str) -> SourceAdapter:
    parsed = httpx.URL(url)
    return next(a for a in default_adapters() if a.matches(parsed))


def run(url: str, network: FakeNetwork) -> tuple[SourceAdapter, JobFetchRecord]:
    adapter = adapter_for(url)
    with make_fetcher(network) as fetcher:
        return adapter, adapter.fetch(url, httpx.URL(url), make_context(fetcher))


@pytest.mark.parametrize(
    ("url", "source", "key"),
    [
        (
            "https://job-boards.greenhouse.io/ExampleCorp/jobs/1234567?gh_src=abc",
            "greenhouse",
            "greenhouse:examplecorp:1234567",
        ),
        (
            "https://job-boards.eu.greenhouse.io/examplecorp/jobs/1234567",
            "greenhouse",
            "greenhouse:examplecorp:1234567",
        ),
        (
            "https://boards.greenhouse.io/embed/job_app?for=examplecorp&token=1234567",
            "greenhouse",
            "greenhouse:examplecorp:1234567",
        ),
        (
            f"https://jobs.ashbyhq.com/exampleorg/{ASHBY_ID.upper()}/application?utm_source=x",
            "ashby",
            f"ashby:exampleorg:{ASHBY_ID}",
        ),
        (
            f"https://jobs.lever.co/examplelever/{LEVER_ID}/apply",
            "lever",
            f"lever:examplelever:{LEVER_ID}",
        ),
        (
            "https://www.linkedin.com/jobs/view/4000000001/?trk=abc&refId=xyz",
            "linkedin",
            "linkedin:4000000001",
        ),
        (
            "https://www.linkedin.com/jobs/view/senior-engineer-2-4000000002/",
            "linkedin",
            "linkedin:4000000002",
        ),
        (
            "https://www.linkedin.com/jobs/view/4000000003/apply/",
            "linkedin",
            "linkedin:4000000003",
        ),
        (
            "https://www.linkedin.com/jobs/view/backend-engineer-4000000004",
            "linkedin",
            "linkedin:4000000004",
        ),
        ("https://lnkd.in/abc123", "linkedin", "page:https://lnkd.in/abc123"),
        (
            "https://careers.example.com/jobs/42?utm_source=li&team=eng#apply",
            "career_page",
            "page:https://careers.example.com/jobs/42?team=eng",
        ),
    ],
)
def test_routing_and_stable_keys(url: str, source: str, key: str) -> None:
    adapter = adapter_for(url)
    assert (adapter.name, adapter.job_key(httpx.URL(url))) == (source, key)


def test_normalize_url_drops_tracking_and_sorts_query() -> None:
    url = httpx.URL("HTTPS://Careers.Example.com/job/7/?b=2&utm_medium=x&a=1&trk=y")
    assert normalize_url(url) == "https://careers.example.com/job/7?a=1&b=2"


def test_normalize_url_keeps_encoded_slashes_distinct() -> None:
    encoded = normalize_url(httpx.URL("https://careers.example.com/teams/a%2Fb/"))
    literal = normalize_url(httpx.URL("https://careers.example.com/teams/a/b/"))
    assert encoded == "https://careers.example.com/teams/a%2Fb"
    assert literal == "https://careers.example.com/teams/a/b"


def test_greenhouse_fetched() -> None:
    network = FakeNetwork(
        {
            GH_API: json_response(
                {
                    "title": "Software Engineer",
                    "company_name": "Example Corp",
                    "content": "&lt;p&gt;Build things &amp;amp; ship.&lt;/p&gt;",
                    "absolute_url": "https://job-boards.greenhouse.io/examplecorp/jobs/1234567",
                }
            )
        }
    )
    _, record = run("https://job-boards.greenhouse.io/examplecorp/jobs/1234567", network)
    assert record.status is FetchStatus.FETCHED
    assert record.title == "Software Engineer"
    assert record.company == "Example Corp"
    assert record.description_text == "Build things & ship."
    assert record.description_hash is not None


def test_greenhouse_closed_job_is_unavailable() -> None:
    network = FakeNetwork({GH_API: json_response({"error": "Job not found"}, 404)})
    _, record = run("https://job-boards.greenhouse.io/examplecorp/jobs/1234567", network)
    assert record.status is FetchStatus.UNAVAILABLE


def test_ashby_fetches_board_once_and_finds_jobs() -> None:
    other_id = "99999999-2222-3333-4444-555555555555"
    network = FakeNetwork(
        {
            ASHBY_API: json_response(
                {
                    "jobs": [
                        {
                            "id": ASHBY_ID,
                            "title": "Backend Engineer",
                            "descriptionPlain": "Work on APIs.",
                            "jobUrl": f"https://jobs.ashbyhq.com/exampleorg/{ASHBY_ID}",
                        }
                    ]
                }
            )
        }
    )
    adapter = adapter_for(f"https://jobs.ashbyhq.com/exampleorg/{ASHBY_ID}")
    with make_fetcher(network) as fetcher:
        ctx = make_context(fetcher)
        found_url = f"https://jobs.ashbyhq.com/exampleorg/{ASHBY_ID}"
        closed_url = f"https://jobs.ashbyhq.com/exampleorg/{other_id}"
        found = adapter.fetch(found_url, httpx.URL(found_url), ctx)
        closed = adapter.fetch(closed_url, httpx.URL(closed_url), ctx)
    assert (found.status, found.title, found.description_text) == (
        FetchStatus.FETCHED,
        "Backend Engineer",
        "Work on APIs.",
    )
    assert closed.status is FetchStatus.UNAVAILABLE
    assert network.requested(ASHBY_API) == 1


def test_lever_combines_description_sections() -> None:
    network = FakeNetwork(
        {
            LEVER_API: json_response(
                {
                    "text": "Platform Engineer",
                    "descriptionPlain": "Intro.",
                    "lists": [{"text": "What you'll do", "content": "<li>Build</li><li>Run</li>"}],
                    "additionalPlain": "Closing.",
                    "hostedUrl": f"https://jobs.lever.co/examplelever/{LEVER_ID}",
                }
            )
        }
    )
    _, record = run(f"https://jobs.lever.co/examplelever/{LEVER_ID}", network)
    assert record.title == "Platform Engineer"
    assert record.description_text == ("Intro.\n\nWhat you'll do\n- Build\n\n- Run\n\nClosing.")


def test_linkedin_is_never_requested() -> None:
    network = FakeNetwork({})
    _, record = run("https://www.linkedin.com/jobs/view/4000000001/", network)
    assert record.status is FetchStatus.NOT_PERMITTED
    assert network.requests == []


def test_career_page_reads_json_ld() -> None:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "Data Engineer",
        "description": "<p>Pipelines.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Example Co"},
    }
    page = f'<script type="application/ld+json">{json.dumps(posting)}</script>'
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": ROBOTS_ALLOW,
            "https://careers.example.com/jobs/42": html_response(page),
        }
    )
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.FETCHED
    assert (record.title, record.company, record.description_text) == (
        "Data Engineer",
        "Example Co",
        "Pipelines.",
    )


def test_career_page_respects_robots_txt() -> None:
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": httpx.Response(
                200, content=b"User-agent: jobsearch-test\nDisallow: /jobs/\n"
            ),
            "https://careers.example.com/jobs/42": html_response("<p>job</p>"),
        }
    )
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.NOT_PERMITTED
    assert network.requested("https://careers.example.com/jobs/42") == 0


def test_career_page_missing_robots_txt_allows_fetch() -> None:
    network = FakeNetwork({"https://careers.example.com/jobs/42": html_response("<p>job</p>", 410)})
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.UNAVAILABLE


def test_career_page_unreachable_robots_txt_disallows() -> None:
    network = FakeNetwork({"https://careers.example.com/robots.txt": httpx.Response(500)})
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.NOT_PERMITTED
    assert network.requested("https://careers.example.com/jobs/42") == 0


def test_career_page_with_gh_jid_uses_greenhouse_api() -> None:
    page = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=examplecorp"></script>'
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": ROBOTS_ALLOW,
            "https://careers.example.com/open-roles": html_response(page),
            GH_API: json_response({"title": "SRE", "content": "&lt;p&gt;Keep it up.&lt;/p&gt;"}),
        }
    )
    _, record = run("https://careers.example.com/open-roles?gh_jid=1234567", network)
    assert record.status is FetchStatus.FETCHED
    assert record.job_key == "greenhouse:examplecorp:1234567"


def test_career_page_with_gh_jid_tries_the_url_company_as_the_board() -> None:
    network = FakeNetwork(
        {
            "https://examplecorp.com/robots.txt": ROBOTS_ALLOW,
            "https://examplecorp.com/careers/1234567": html_response("<div id='app'></div>"),
            GH_API: json_response({"title": "SRE", "content": "&lt;p&gt;Keep it up.&lt;/p&gt;"}),
        }
    )
    _, record = run("https://examplecorp.com/careers/1234567?gh_jid=1234567", network)
    assert record.status is FetchStatus.FETCHED
    assert record.job_key == "greenhouse:examplecorp:1234567"


def test_career_page_guessed_board_miss_is_not_reported_as_closed() -> None:
    network = FakeNetwork(
        {
            "https://examplecorp.com/robots.txt": ROBOTS_ALLOW,
            "https://examplecorp.com/careers/1234567": html_response("<div id='app'></div>"),
        }
    )
    _, record = run("https://examplecorp.com/careers/1234567?gh_jid=1234567", network)
    assert record.status is FetchStatus.NO_DETAILS
    assert record.job_key == "page:https://examplecorp.com/careers/1234567?gh_jid=1234567"


def test_career_page_without_job_data_needs_manual_text() -> None:
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": ROBOTS_ALLOW,
            "https://careers.example.com/jobs/42": html_response("<div id='app'></div>"),
        }
    )
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.NO_DETAILS


def test_career_page_blocked_by_site_is_failed() -> None:
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": ROBOTS_ALLOW,
            "https://careers.example.com/jobs/42": html_response("denied", 403),
        }
    )
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.FAILED
    assert record.detail == "page returned HTTP 403"


def _hosted_page(company: str) -> httpx.Response:
    posting = {"@type": "JobPosting", "hiringOrganization": {"name": company}}
    return html_response(f'<script type="application/ld+json">{json.dumps(posting)}</script>')


LEVER_PAGE = f"https://jobs.lever.co/examplelever/{LEVER_ID}"
LEVER_JOB = {"text": "Platform Engineer", "descriptionPlain": "Intro.", "hostedUrl": LEVER_PAGE}


def test_lever_reads_company_from_hosted_page() -> None:
    network = FakeNetwork(
        {
            LEVER_API: json_response(LEVER_JOB),
            "https://jobs.lever.co/robots.txt": ROBOTS_ALLOW,
            LEVER_PAGE: _hosted_page("Example Lever Inc."),
        }
    )
    _, record = run(LEVER_PAGE, network)
    assert (record.status, record.company) == (FetchStatus.FETCHED, "Example Lever Inc.")


def test_ashby_reads_company_from_hosted_page() -> None:
    page = f"https://jobs.ashbyhq.com/exampleorg/{ASHBY_ID}"
    network = FakeNetwork(
        {
            ASHBY_API: json_response(
                {"jobs": [{"id": ASHBY_ID, "title": "T", "descriptionPlain": "D", "jobUrl": page}]}
            ),
            "https://jobs.ashbyhq.com/robots.txt": ROBOTS_ALLOW,
            page: _hosted_page("Example Org"),
        }
    )
    _, record = run(page, network)
    assert (record.status, record.company) == (FetchStatus.FETCHED, "Example Org")


def test_failed_company_lookup_keeps_the_job() -> None:
    network = FakeNetwork(
        {
            LEVER_API: json_response(LEVER_JOB),
            "https://jobs.lever.co/robots.txt": ROBOTS_ALLOW,
            LEVER_PAGE: html_response("unavailable", 503),
        }
    )
    _, record = run(LEVER_PAGE, network)
    assert (record.status, record.title, record.company) == (
        FetchStatus.FETCHED,
        "Platform Engineer",
        None,
    )


def test_company_lookup_respects_robots_txt() -> None:
    network = FakeNetwork(
        {
            LEVER_API: json_response(LEVER_JOB),
            "https://jobs.lever.co/robots.txt": httpx.Response(
                200, content=b"User-agent: *\nDisallow: /\n"
            ),
            LEVER_PAGE: _hosted_page("Should Not Be Read"),
        }
    )
    _, record = run(LEVER_PAGE, network)
    assert record.company is None
    assert network.requested(LEVER_PAGE) == 0


def test_closed_greenhouse_job_gets_the_board_company_name() -> None:
    network = FakeNetwork(
        {
            GH_API: json_response({"error": "Job not found"}, 404),
            "https://boards-api.greenhouse.io/v1/boards/examplecorp": json_response(
                {"name": "Example Corp", "content": ""}
            ),
        }
    )
    _, record = run("https://job-boards.greenhouse.io/examplecorp/jobs/1234567", network)
    assert (record.status, record.company, record.company_source) == (
        FetchStatus.UNAVAILABLE,
        "Example Corp",
        CompanySource.JOB_BOARD,
    )


def test_closed_lever_job_gets_the_board_page_title() -> None:
    network = FakeNetwork(
        {
            "https://jobs.lever.co/robots.txt": ROBOTS_ALLOW,
            "https://jobs.lever.co/examplelever": html_response(
                "<html><head><title>  Example Lever Inc. </title></head></html>"
            ),
        }
    )
    _, record = run(LEVER_PAGE, network)
    assert (record.status, record.company, record.company_source) == (
        FetchStatus.UNAVAILABLE,
        "Example Lever Inc.",
        CompanySource.JOB_BOARD,
    )


def test_closed_ashby_job_gets_the_board_organization_name() -> None:
    app_data = {"organization": {"organizationId": "x", "name": 'Example "Org"'}}
    network = FakeNetwork(
        {
            ASHBY_API: json_response({"jobs": []}),
            "https://jobs.ashbyhq.com/robots.txt": ROBOTS_ALLOW,
            "https://jobs.ashbyhq.com/exampleorg": html_response(
                f"<script>window.__appData = {json.dumps(app_data)};</script>"
            ),
        }
    )
    _, record = run(f"https://jobs.ashbyhq.com/exampleorg/{ASHBY_ID}", network)
    assert (record.status, record.company, record.company_source) == (
        FetchStatus.UNAVAILABLE,
        'Example "Org"',
        CompanySource.JOB_BOARD,
    )


def test_board_name_is_looked_up_once_per_board() -> None:
    board_api = "https://boards-api.greenhouse.io/v1/boards/examplecorp"
    network = FakeNetwork({board_api: json_response({"name": "Example Corp"})})
    adapter = adapter_for("https://job-boards.greenhouse.io/examplecorp/jobs/1")
    with make_fetcher(network) as fetcher:
        ctx = make_context(fetcher)
        for job_id in ("1", "2"):
            url = f"https://job-boards.greenhouse.io/examplecorp/jobs/{job_id}"
            adapter.fetch(url, httpx.URL(url), ctx)
    assert network.requested(board_api) == 1


def _json_ld(*postings: dict[str, object]) -> httpx.Response:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(p)}</script>' for p in postings
    )
    return html_response(scripts)


def _posting(title: str, url: str) -> dict[str, object]:
    return {"@type": "JobPosting", "title": title, "description": f"<p>{title}.</p>", "url": url}


def test_career_page_listing_several_jobs_takes_only_the_requested_one() -> None:
    page = _json_ld(
        _posting("Other Job", "https://careers.example.com/jobs/41"),
        _posting("Wanted Job", "https://careers.example.com/jobs/42/"),
    )
    network = FakeNetwork(
        {"https://careers.example.com/robots.txt": ROBOTS_ALLOW,
         "https://careers.example.com/jobs/42": page}
    )  # fmt: skip
    _, record = run("https://careers.example.com/jobs/42", network)
    assert (record.status, record.title) == (FetchStatus.FETCHED, "Wanted Job")


def test_career_page_listing_without_the_requested_job_has_no_details() -> None:
    page = _json_ld(
        _posting("Job A", "https://careers.example.com/jobs/1"),
        _posting("Job B", "https://careers.example.com/jobs/2"),
    )
    network = FakeNetwork(
        {"https://careers.example.com/robots.txt": ROBOTS_ALLOW,
         "https://careers.example.com/jobs": page}
    )  # fmt: skip
    _, record = run("https://careers.example.com/jobs", network)
    assert record.status is FetchStatus.NO_DETAILS


def test_career_page_board_id_wins_over_unrelated_json_ld() -> None:
    page = _json_ld(_posting("Unrelated Job", "https://careers.example.com/jobs/9"))
    page = html_response(
        page.text + '<script src="https://boards.greenhouse.io/embed/job_board/js?for=examplecorp">'
        "</script>"
    )
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": ROBOTS_ALLOW,
            "https://careers.example.com/open-roles": page,
            GH_API: json_response({"title": "SRE", "content": "&lt;p&gt;Keep it up.&lt;/p&gt;"}),
        }
    )
    _, record = run("https://careers.example.com/open-roles?gh_jid=1234567", network)
    assert (record.job_key, record.title) == ("greenhouse:examplecorp:1234567", "SRE")


def test_career_page_redirect_is_checked_against_the_new_origins_robots_txt() -> None:
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": ROBOTS_ALLOW,
            "https://careers.example.com/jobs/42": httpx.Response(
                302, headers={"location": "https://jobs.example.org/42"}
            ),
            "https://jobs.example.org/robots.txt": httpx.Response(
                200, content=b"User-agent: *\nDisallow: /\n"
            ),
            "https://jobs.example.org/42": html_response("<p>job</p>"),
        }
    )
    _, record = run("https://careers.example.com/jobs/42", network)
    assert record.status is FetchStatus.NOT_PERMITTED
    assert network.requested("https://jobs.example.org/42") == 0


def test_career_page_robots_txt_wildcards_are_honored() -> None:
    network = FakeNetwork(
        {
            "https://careers.example.com/robots.txt": httpx.Response(
                200, content=b"User-agent: *\nDisallow: /*/apply$\n"
            ),
            "https://careers.example.com/jobs/42/apply": html_response("<p>job</p>"),
        }
    )
    _, record = run("https://careers.example.com/jobs/42/apply", network)
    assert record.status is FetchStatus.NOT_PERMITTED
    assert network.requested("https://careers.example.com/jobs/42/apply") == 0
