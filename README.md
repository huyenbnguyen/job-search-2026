# jobsearch

A local, single-user job search tool with evidence-backed resume tailoring. Your records (fetched jobs, logs, and later your resume and labels) are stored only on your machine; what leaves it is listed under [Privacy](#privacy). See `docs/scoping.md` for the design and `docs/roadmap.md` for the phases.

## Setup

```bash
uv sync
cp config/config.example.yaml config/config.yaml   # optional; all keys have defaults
```

## Fetch details for jobs you applied to

Put the job URLs in a CSV, one per row in the first column, for example `data/applied_jobs.csv`. `data/` is gitignored. Then run:

```bash
uv run jobsearch fetch-applied data/applied_jobs.csv            # writes data/applied_jobs.jsonl
uv run jobsearch fetch-applied data/applied_jobs.csv --limit 10 # try the first 10
```

Each line of the output is one job:
- `job_key`: stable dedupe key, such as `greenhouse:<board>:<id>`
- `status`: `fetched`, `unavailable`, `not_permitted`, `no_details`, or `failed`
- title, company, and plain-text description
- `description_hash`
- source URL and retrieval time

The command prints the jobs it couldn't fetch. Those need their text pasted in by hand (a later Phase 1 step).

What gets fetched:
- Greenhouse, Lever, and Ashby, through their public job-board APIs.
- Other career pages: one request each, reading the job data the page publishes (schema.org JobPosting), and only where robots.txt allows it. Redirects are checked against the robots.txt of the site they lead to.
- LinkedIn: never, because its terms prohibit automated access. That includes a redirect to LinkedIn from another site.

Requests are SSRF-checked, size- and time-limited, and spaced out per host. JSON logs go to `data/logs/jobsearch.log`; they identify jobs by a short hash, not by URL.

## Privacy

Records are stored locally. The only data sent anywhere today is the HTTP requests `fetch-applied` makes to job sources:
- **Who receives it:** the sites in your CSV (company career pages), the sites they redirect to, and the public APIs of Greenhouse, Lever, and Ashby.
- **What they see:** the job URL or its board and job ID, your IP address, the configured `user_agent` (no name or email), and standard headers (`Accept`, `Accept-Encoding`). Each career-page site also gets a request for its `robots.txt`.
- **What they don't see:** no resume text, labels, or other jobs from your list are sent with these requests.

These requests are separate from LLM calls, which arrive in a later Phase 1 step. Those will send job and resume text to the LLM provider you configure, and this section will list what each call sends.

## Development

```bash
uv run pytest                     # all tests
uv run pytest tests/unit/test_fetch.py::test_redirect_limit   # one test
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
