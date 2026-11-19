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

## Load your resume

Keep the PDF in `docs/resume/` or `data/`; both are gitignored. Only text-based PDFs work. A scanned image has no text to extract.

```bash
uv run jobsearch resume load docs/resume/resume.pdf   # extract and store the text
uv run jobsearch resume show                          # print the newest version's text, as scoring sees it
uv run jobsearch resume list                          # every stored version
```

Each distinct text is stored once in `resume_versions` and never overwritten. Loading the same text again, even from a renamed file, adds nothing. Editing the resume and loading it again adds a new version. Model scores record which version they used.

## Keyword baseline

A simple, non-LLM score that every model has to beat:

```bash
uv run jobsearch score baseline              # score all jobs with a description against your newest resume
uv run jobsearch score baseline --resume 1   # or a specific resume version
```

It counts the skills from a fixed list that the job description mentions, and how many of those your resume also mentions. That share becomes a whole-number score from 1 to 5: below 20% scores 1, and 80% or more scores 5. The thresholds are `scoring.keyword_baseline.threshold_percents` in config.

Scores go to `fit_predictions` with provider `baseline`. The command shows scores only for jobs you've already labeled, so the baseline can't anchor your own scores.

How skills are matched, how the share maps onto 1–5 (with worked examples), the known limits, and what's stored: [docs/baseline_job_matching.md](docs/baseline_job_matching.md).

## Database

Jobs are stored in a local SQLite file, by default `~/.local/share/jobsearch/jobsearch.db`. It lives outside the repository; set `database.path` in `config/config.yaml` to move it.

```bash
uv run jobsearch db upgrade                                # create the database / apply migrations
uv run jobsearch db import-jobs data/applied_jobs.jsonl    # import fetched jobs; safe to rerun
```

Tables:
- `jobs`: one row per distinct job (`job_key`), with `applied` set for jobs you applied to. `got_interviewed` is yours to set: `1` if you got an interview, otherwise `0` (the default). It also holds the best-known `company`, plus `company_source`, which says where that name came from (most reliable first):
  - `user`: you set it with `db set-company`. Never replaced automatically.
  - `job_posting`: the job's own data.
  - `job_board`: the company's Greenhouse board name, or its Lever/Ashby board page. This still works after a job closes.
  - `url`: a slug taken from the URL, such as `exampleco` or `acme-robotics`. A hint, not a verified name.

  A name is only replaced by one from a more reliable source.

  `jobs` also holds the title and description from the first successful fetch. They're never overwritten, so your labels and model scores always refer to the same text.
- `job_fetch_attempts`: every fetch outcome, including closed or blocked jobs. Its `description_hash` shows if a later fetch returned different text.
- `human_labels`: your fit score (1–5) and would-apply answer for a job, with the rubric version and optional `notes` on your reasoning. Append-only: re-scoring a job adds a row.
- `fit_predictions`: model and baseline fit scores (1–5), each with the job scored and the model, prompt, rubric and resume versions, plus `details` (JSON) on how the score was reached. Kept apart from your labels.
- `resume_versions`: the extracted text of each distinct resume, with its hash, file name and the extractor used.
- `jobs_overview` (view): each job with its description, latest fetch status, and the latest `human_fit_score`, `human_would_apply` and `ai_fit_score`. `ai_fit_score` is the latest prediction from any scorer, including the keyword baseline. Hide that column while you label new jobs.

Set or correct a company by hand. LinkedIn jobs need this, because their URLs carry no company:

```bash
uv run jobsearch db set-company 42 "Example Corp"   # job_id from jobs_overview
```

Query it with the `sqlite3` CLI:

```bash
sqlite3 -header -column ~/.local/share/jobsearch/jobsearch.db \
  "SELECT job_id, title, company FROM jobs_overview WHERE last_status = 'fetched';"
```

### Backup and restore

Back up with SQLite's online backup, which is safe while the app is running. A plain file copy isn't safe: it can catch the database mid-write and misses changes still in the `-wal` file.

```bash
sqlite3 ~/.local/share/jobsearch/jobsearch.db ".backup 'jobsearch-2026-11-19.db'"
```

The backup location and schedule are still TBD.

To restore:
1. **Pick the backup.** Use the newest one from before the problem. Name backups by date so this is easy.
2. **Stop everything that uses the database.** Wait for any `jobsearch` command to finish and pause scheduled runs (from Phase 3). Restoring while something else has the file open can mix the two states.
3. **Check the backup:** `sqlite3 <backup> "PRAGMA integrity_check;"` should print `ok`.
4. **Restore it to the configured path** (`database.path`, by default `~/.local/share/jobsearch/jobsearch.db`):
   ```bash
   sqlite3 ~/.local/share/jobsearch/jobsearch.db ".restore 'jobsearch-2026-11-19.db'"
   ```
   `.restore` writes through SQLite, so the database's `-wal` and `-shm` files stay consistent. Don't copy the backup over the file by hand.
5. **Verify:** `uv run jobsearch db upgrade` brings an older backup up to the current schema. Then `sqlite3 ~/.local/share/jobsearch/jobsearch.db "PRAGMA integrity_check; SELECT COUNT(*) FROM jobs;"` should print `ok` and the job count you expect.

`tests/integration/test_database.py::test_restore_from_backup` runs these steps through the same SQLite backup API that `.backup` and `.restore` use.

## Development

```bash
uv run pytest                     # all tests
uv run pytest tests/unit/test_fetch.py::test_redirect_limit   # one test
uv run ruff check . && uv run ruff format --check . && uv run mypy
```
