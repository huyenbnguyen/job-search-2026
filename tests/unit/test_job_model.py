import pytest
from pydantic import ValidationError

from jobsearch.models.job import FetchStatus, JobFetchRecord
from tests.unit.helpers import FIXED_NOW


def record(description: str | None) -> JobFetchRecord:
    return JobFetchRecord(
        run_id="test-run",
        source_url="https://careers.example.com/jobs/1",
        job_key="page:https://careers.example.com/jobs/1",
        source="career_page",
        status=FetchStatus.NO_DETAILS,
        retrieved_at=FIXED_NOW,
        description_text=description,
    )


@pytest.mark.parametrize("description", ["", "Some text.", None])
def test_serialized_record_round_trips(description: str | None) -> None:
    original = record(description)
    data = original.model_dump(mode="json")
    assert (data["description_hash"] is None) is (description is None)
    assert JobFetchRecord.model_validate(data) == original


@pytest.mark.parametrize(
    ("description", "stored_hash"),
    [("", None), ("Some text.", "0" * 64), (None, "0" * 64)],
)
def test_hash_that_does_not_match_the_text_is_rejected(
    description: str | None, stored_hash: str | None
) -> None:
    data = record(description).model_dump(mode="json")
    data["description_hash"] = stored_hash
    with pytest.raises(ValidationError, match="description_hash"):
        JobFetchRecord.model_validate(data)
