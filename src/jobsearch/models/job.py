"""Fetched job records.

A `JobFetchRecord` is the staging shape for the SQLite import:
- `job_key` is the stable dedupe key (unique constraint on the `jobs` table).
- `description_hash` identifies the text, so a fetch that returns different text can
  be detected. The job keeps its first description.
- `source_url` and `retrieved_at` are the provenance every job record keeps.
- `company_source` says where `company` came from, most reliable first: see CompanySource.
"""

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, computed_field, model_validator


class FetchStatus(StrEnum):
    FETCHED = "fetched"
    # The source says the posting is gone (HTTP 404/410, or no longer on the job board).
    UNAVAILABLE = "unavailable"
    # The source's terms or robots.txt don't allow automated access.
    NOT_PERMITTED = "not_permitted"
    # The page loaded, but it has no machine-readable job data (often a JavaScript-only page).
    NO_DETAILS = "no_details"
    # A network error, a blocked URL, or an unexpected response.
    FAILED = "failed"


class CompanySource(StrEnum):
    """Where a company name came from. A more reliable source may replace a less reliable one."""

    # Entered by the user. Never replaced automatically.
    USER = "user"
    # The job posting's own data (an API field or the page's JobPosting hiringOrganization).
    JOB_POSTING = "job_posting"
    # The company's job board: its Greenhouse board name or its Lever/Ashby board page.
    JOB_BOARD = "job_board"
    # Derived from the URL: an ATS tenant or the site's domain. A hint, not a verified name.
    URL = "url"

    @property
    def rank(self) -> int:
        return _COMPANY_SOURCE_RANK[self]


_COMPANY_SOURCE_RANK = {
    CompanySource.USER: 4,
    CompanySource.JOB_POSTING: 3,
    CompanySource.JOB_BOARD: 2,
    CompanySource.URL: 1,
}


class JobFetchRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    source_url: str
    job_key: str
    source: str
    status: FetchStatus
    retrieved_at: AwareDatetime
    title: str | None = None
    company: str | None = None
    company_source: CompanySource | None = None
    description_text: str | None = None
    posting_url: str | None = None
    # Short reason for a non-fetched status. Never page content.
    detail: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def description_hash(self) -> str | None:
        if self.description_text is None:
            return None
        return _text_hash(self.description_text)

    @model_validator(mode="before")
    @classmethod
    def _check_stored_hash(cls, data: Any) -> Any:
        """Accept a serialized record back, as long as its stored hash still matches."""
        if isinstance(data, dict) and data.get("company") and not data.get("company_source"):
            # Records written before company_source existed got companies from posting data.
            data = {**data, "company_source": CompanySource.JOB_POSTING}
        if isinstance(data, dict) and "description_hash" in data:
            data = dict(data)
            stored = data.pop("description_hash")
            text = data.get("description_text")
            expected = _text_hash(text) if isinstance(text, str) else None
            if stored != expected:
                raise ValueError("description_hash doesn't match description_text")
        return data

    @model_validator(mode="after")
    def _fetched_has_content(self) -> Self:
        if self.status is FetchStatus.FETCHED and not (self.title and self.description_text):
            raise ValueError("a fetched record needs a title and description_text")
        if (self.company is None) != (self.company_source is None):
            raise ValueError("company and company_source must be set together")
        return self


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> datetime:
    return datetime.now(UTC)
