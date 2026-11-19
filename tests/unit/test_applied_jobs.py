import json
from pathlib import Path

import httpx

from jobsearch.models.job import CompanySource, FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.applied_jobs import fetch_jobs, read_job_urls, write_jsonl
from jobsearch.services.ingestion.sources import default_adapters
from tests.unit.helpers import FakeNetwork, json_response, make_context, make_fetcher

GH_URL = "https://job-boards.greenhouse.io/examplecorp/jobs/1234567"
GH_API = "https://boards-api.greenhouse.io/v1/boards/examplecorp/jobs/1234567"


def test_read_job_urls_handles_crlf_blank_rows_and_bom(tmp_path: Path) -> None:
    csv_path = tmp_path / "jobs.csv"
    csv_path.write_bytes(
        b"\xef\xbb\xbfhttps://a.example.com/1\r\n\r\n  https://b.example.com/2 \r\n"
    )
    assert read_job_urls(csv_path) == ["https://a.example.com/1", "https://b.example.com/2"]


def collect(urls: list[str], network: FakeNetwork) -> list[JobFetchRecord]:
    with make_fetcher(network) as fetcher:
        return list(fetch_jobs(urls, default_adapters(), make_context(fetcher)))


def test_duplicates_are_fetched_once() -> None:
    network = FakeNetwork(
        {GH_API: json_response({"title": "T", "content": "&lt;p&gt;D&lt;/p&gt;"})}
    )
    records = collect([GH_URL, GH_URL + "?gh_src=x", GH_URL + "/"], network)
    assert [r.job_key for r in records] == ["greenhouse:examplecorp:1234567"]
    assert network.requested(GH_API) == 1


def test_one_failure_does_not_stop_the_run() -> None:
    def explode(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"not json", headers={"content-type": "application/json"}
        )

    lever_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    network = FakeNetwork(
        {
            f"https://api.lever.co/v0/postings/examplelever/{lever_id}": explode,
            GH_API: json_response({"title": "T", "content": "&lt;p&gt;D&lt;/p&gt;"}),
        }
    )
    records = collect(
        [f"https://jobs.lever.co/examplelever/{lever_id}", "not a url", GH_URL], network
    )
    assert [(r.source, r.status) for r in records] == [
        ("lever", FetchStatus.FAILED),
        ("unknown", FetchStatus.FAILED),
        ("greenhouse", FetchStatus.FETCHED),
    ]
    assert records[0].detail == "unexpected JSONDecodeError"


def test_blocked_url_is_recorded_as_failed() -> None:
    with make_fetcher(FakeNetwork({}), resolver=lambda host, port: ["127.0.0.1"]) as fetcher:
        records = list(fetch_jobs([GH_URL], default_adapters(), make_context(fetcher)))
    assert records[0].status is FetchStatus.FAILED
    assert "non-public" in (records[0].detail or "")


def test_write_jsonl_round_trips(tmp_path: Path) -> None:
    network = FakeNetwork(
        {GH_API: json_response({"title": "T", "content": "&lt;p&gt;D&lt;/p&gt;"})}
    )
    records = collect([GH_URL], network)
    out = tmp_path / "nested" / "jobs.jsonl"
    assert write_jsonl(records, out) == 1
    [line] = out.read_text(encoding="utf-8").splitlines()
    data = json.loads(line)
    assert data["description_hash"] == records[0].description_hash
    assert data["retrieved_at"] == "2026-01-15T12:00:00Z"
    assert JobFetchRecord.model_validate(data) == records[0]
    assert list(tmp_path.joinpath("nested").iterdir()) == [out]


def test_tampered_hash_is_rejected() -> None:
    import pytest
    from pydantic import ValidationError

    network = FakeNetwork(
        {GH_API: json_response({"title": "T", "content": "&lt;p&gt;D&lt;/p&gt;"})}
    )
    data = json.loads(collect([GH_URL], network)[0].model_dump_json())
    data["description_text"] = "changed"
    with pytest.raises(ValidationError):
        JobFetchRecord.model_validate(data)


def test_url_hint_fills_company_when_nothing_better_was_found() -> None:
    network = FakeNetwork(
        {
            "https://careers.exampleco.com/robots.txt": httpx.Response(404),
            "https://careers.exampleco.com/jobs/1": httpx.Response(404),
        }
    )
    records = collect(
        ["https://careers.exampleco.com/jobs/1", "https://www.linkedin.com/jobs/view/1"], network
    )
    assert [(r.company, r.company_source) for r in records] == [
        ("exampleco", CompanySource.URL),
        (None, None),
    ]


def test_company_and_company_source_go_together() -> None:
    import pytest
    from pydantic import ValidationError

    base = collect([GH_URL], FakeNetwork({GH_API: json_response({"title": "T", "content": "D"})}))[
        0
    ]
    with pytest.raises(ValidationError):
        JobFetchRecord.model_validate(
            {**base.model_dump(exclude={"description_hash"}), "company": None,
             "company_source": "url"}
        )  # fmt: skip


def test_records_written_before_company_source_load_as_job_posting() -> None:
    network = FakeNetwork(
        {GH_API: json_response({"title": "T", "content": "D", "company_name": "X"})}
    )
    data = json.loads(collect([GH_URL], network)[0].model_dump_json())
    del data["company_source"]
    assert JobFetchRecord.model_validate(data).company_source is CompanySource.JOB_POSTING
