# jobsearch

A local, single-user job search tool with evidence-backed resume tailoring. All data stays on your machine. See `docs/scoping.md` for the design and `docs/roadmap.md` for the phases.

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
- Other career pages: one request each, reading the job data the page publishes (schema.org JobPosting), and only where robots.txt allows it.
- LinkedIn: never, because its terms prohibit automated access.

Requests are SSRF-checked, size- and time-limited, and spaced out per host. JSON logs go to `data/logs/jobsearch.log`.

## Development

```bash
uv run pytest                     # all tests
uv run pytest tests/unit/test_fetch.py::test_redirect_limit   # one test
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
