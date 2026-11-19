# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Current state

Phase 1a is in progress. `src/jobsearch/` has typed config, an SSRF-safe fetcher, and job source adapters, plus the `fetch-applied` command, which stages fetched jobs as JSONL (`JobFetchRecord`) and `db import-jobs` loads them into SQLite (`~/.local/share/jobsearch/jobsearch.db` by default, set by `database.path`). The schema lives in `models/tables.py`, with Alembic migrations in `migrations/`. `alembic.ini` has no URL: `migrations/env.py` reads it from the app config. Tests point at a temporary database by passing `DatabaseConfig` through `config.attributes["database"]`. `resume load` extracts a text-based PDF resume (pypdf, layout mode) into `resume_versions`, one row per distinct text, keyed by its hash (`services/profiles.py`). `data/` and `docs/resume/` hold personal data and are gitignored.

## Where the design lives

- `docs/scoping.md` is the source of truth. When another doc or a skill disagrees with it, `scoping.md` wins, and you should flag the conflict.
- `docs/roadmap.md` has seven phases, each with "Decide before starting" items and a checklist for when it's done. Check those decisions before starting a phase. If one is still open, ask the user.
- `docs/tech-stack-decisions.md` is the decision log for tooling: what was chosen, what was rejected, and when to revisit. Add new decisions there in the same format.
- Source adapters live in `src/jobsearch/services/ingestion/sources/`. They're tried in the order `default_adapters()` lists them. `career_page` matches any URL, so it goes last. When a career page embeds a Greenhouse or Ashby board, it hands off to that board's adapter.

## Phase 1 scoring design (spans several docs)

- The user's labels are ground truth. There are two separate labels, a 1–5 fit score and would-apply (yes/no), each tagged with its rubric version. The user labels a job before any model score is shown.
- The model scores a job one requirement at a time. Each requirement gets a status (met, partial, not met, unclear) and a quoted resume line. Then it gives an overall 1–5 score with a rationale. Code checks every quote against the resume text, and fabricated quotes count as a metric.
- A deterministic coverage score is computed from the requirement statuses. A non-LLM keyword-overlap baseline goes in every comparison.
- Every experiment records the model ID and the prompt, rubric, and dataset versions. Pick a configuration by agreement with the user first, then consistency, then cost and latency.
