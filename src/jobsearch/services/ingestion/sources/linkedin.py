"""LinkedIn: never fetched.

LinkedIn's User Agreement prohibits automated access to its site, so these links
are recorded as not permitted and left for the paste-text fallback.
"""

import re

import httpx

from jobsearch.models.job import FetchStatus, JobFetchRecord
from jobsearch.services.ingestion.fetch import is_linkedin_host
from jobsearch.services.ingestion.sources.base import FetchContext, SourceAdapter, normalize_url

# The ID is the last hyphen-separated part of the slug: /jobs/view/<slug>-<id>/ or /jobs/view/<id>.
_JOB_VIEW = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)(?:/|$)")


class LinkedInAdapter(SourceAdapter):
    name = "linkedin"

    def matches(self, url: httpx.URL) -> bool:
        return is_linkedin_host(url.host)

    def job_key(self, url: httpx.URL) -> str:
        match = _JOB_VIEW.search(url.path)
        job_id = match[1] if match else url.params.get("currentJobId")
        if job_id and job_id.isdigit():
            return f"linkedin:{job_id}"
        return f"page:{normalize_url(url)}"

    def fetch(self, source_url: str, url: httpx.URL, ctx: FetchContext) -> JobFetchRecord:
        return self.record(
            ctx,
            source_url=source_url,
            job_key=self.job_key(url),
            status=FetchStatus.NOT_PERMITTED,
            detail="LinkedIn's terms prohibit automated access; paste the job text instead",
        )
