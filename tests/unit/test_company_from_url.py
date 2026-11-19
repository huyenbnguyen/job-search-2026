import httpx
import pytest

from jobsearch.services.ingestion.sources.company_from_url import company_from_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://careers.example.com/jobs/1", "example"),
        ("https://www.example.co.uk/careers/1", "example"),
        ("https://exampleco.wd5.myworkdayjobs.com/External/job/1", "exampleco"),
        ("https://wd1.myworkdaysite.com/en-US/recruiting/exampleco/site/job/1", "exampleco"),
        ("https://exampleco.fa.us2.oraclecloud.com/hcmUI/job/1", "exampleco"),
        ("https://exampleco.avature.net/careers/1", "exampleco"),
        ("https://jobs.smartrecruiters.com/ExampleCo/123-engineer", "exampleco"),
        ("https://ats.rippling.com/en-AU/exampleco-job-board/jobs/1", "exampleco"),
        ("https://app.careerpuck.com/job-board/exampleco/job/1", "exampleco"),
        ("https://job-boards.greenhouse.io/exampleco/jobs/1", "exampleco"),
        ("https://careersatexampleco.com/jobs/1", "exampleco"),
        ("https://www.examplecocareers.com/job/1", "exampleco"),
        ("https://joinexampleco.com/1", "exampleco"),
        ("https://www.example.careers/job/1", "example"),
        ("https://example.jobs/1", "example"),
        (
            "https://www.linkedin.com/jobs/view/backend-engineer-at-example-co-4000000001/",
            "example-co",
        ),
        ("https://www.linkedin.com/jobs/view/engineer-at-scale-at-example-4000000001", "example"),
    ],
)
def test_derives_company_slug(url: str, expected: str) -> None:
    assert company_from_url(httpx.URL(url)) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/jobs/view/1",
        "https://www.linkedin.com/jobs/view/4000000001/?trk=abc",
        "https://www.indeed.com/viewjob?jk=1",
        "https://localhost/x",
    ],
)
def test_no_company_for_aggregators_or_bare_hosts(url: str) -> None:
    assert company_from_url(httpx.URL(url)) is None
