# Roadmap

Delivery plan for the AI job search tool. The design, rationale, and metrics are in `scoping.md`; if this roadmap and `scoping.md` disagree, `scoping.md` wins.

**Setup:** one user, all data local, Python CLI, SQLite. Cloud and locally run LLMs are both allowed.
**Rule for every phase:** it ends in something you can run from the CLI and use right away.
**Dates:** not set yet. Estimate a phase before starting it.

## Overview

| Phase | Milestone | What you can do when it's done | Status |
|---|---|---|---|
| 1 | LLM fit-scoring spike | Score jobs yourself and see which model and prompt agree with you, and at what cost | Not started |
| 2 | Job discovery and search | Run a search and review a filtered, ranked shortlist | Not started |
| 3 | Scheduled runs and evaluation loop | Let the tool search on a schedule; your decisions improve model and prompt choice | Not started |
| 4 | Evidence-backed resume tailoring | Get a tailored, evidence-checked resume draft for a chosen job | Not started |
| 5 | Hardening | Leave scheduled runs unattended with confidence | Not started |
| 6 | Local web UI | Use the tool in a browser on localhost | Not started |
| 7 | Work authorization and sponsorship | Filter and rank by sponsorship requirements | Not started |

Phase 1 is the main spike: whether an LLM can judge job fit the way you do is the biggest unknown, so it's tested before anything is built around it.

---

## Phase 1: LLM fit-scoring spike

**Goal:** find out whether LLMs can score how well a job matches your resume the way you would, and which model and prompt do it best for the cost.

**Decide before starting**
- [ ] Final wording of the 1–5 rubric anchors (draft in `scoping.md`, Section 2)
- [ ] Which 2–3 models to compare, including whether to try a local one
- [ ] Thin in-house provider adapters or a multi-provider library
- [ ] Spending budget for experiments
- [ ] Each cloud provider's API data-retention and training-use settings checked

The steps below are a proposed breakdown; each one is usable on its own.

**1a. Ingest and label**
- [ ] Project setup: package layout, typed config, SQLite with WAL mode, Alembic migrations, pytest, CI
- [ ] `ingest` command: fetch a job URL into a normalized, versioned record with source URL and timestamp; safe against SSRF
- [ ] Paste-text fallback for pages that can't be fetched
- [ ] Load your resume (text-based PDF or DOCX) as a versioned profile
- [ ] `label` command: record a fit score (1–5) and would-apply (yes/no) for each job, with no model scores shown
- [ ] Rubric stored as a versioned file; rubric version saved with every label

*Usable outcome:* your applied-to jobs are in the database with your own scores.

**1b. One model scores a job**
- [ ] Provider-agnostic LLM interface and a first adapter
- [ ] Fit-scoring prompt (`fit_score_v1`): requirement-by-requirement status (met, partial, not met, unclear) with a quoted resume line, then an overall 1–5 score and rationale
- [ ] Pydantic validation; code checks every quote against the resume
- [ ] Deterministic coverage score computed from the requirement statuses
- [ ] Tokens, latency, and estimated cost recorded for every call

*Usable outcome:* score any stored job with one model and see its reasoning.

**1c. Compare models and prompts**
- [ ] Adapters for the remaining candidate models
- [ ] Non-LLM baseline (keyword/skill overlap mapped onto 1–5)
- [ ] Experiment runner over models × prompt versions × jobs, with a repeated subset to measure run-to-run variance
- [ ] Report: mean absolute error, within-one agreement, mean signed error, Spearman correlation, confusion matrix, variance, schema validity, fabricated-quote rate, cost, latency, and bootstrap confidence intervals
- [ ] Every experiment records model ID, prompt, rubric and dataset versions

*Usable outcome:* a report showing which model and prompt agree with you most, and at what cost.

**Labeling work for you during Phase 1**
- Add lower-fit jobs (scores 1–3) so the set covers the whole scale; applied-to jobs skew toward 4–5.
- After a week or two, re-score about 10 jobs without looking at your earlier scores, to measure your own consistency.

**Done when**
- [ ] The comparison report runs end to end from the CLI, baseline included
- [ ] A scoring configuration is chosen (agreement first, then consistency, then cost and latency) and recorded in the decision log
- [ ] Results and their uncertainty are written up in the README, without claiming more than the sample supports

---

## Phase 2: Job discovery and search

**Goal:** find new jobs from sources and rank the ones that pass your filters.

**Decide before starting**
- [ ] Which job sources to integrate first, after checking their access terms

**Deliverables**
- [ ] Source adapters for permitted sources only, behind a common interface; one failing source doesn't fail the run
- [ ] Deduplication on a stable job key
- [ ] Preferences: roles, seniority, country, city, remote/hybrid/onsite, each marked required or preferred
- [ ] Requirement extraction into structured fields (location, work arrangement, skills, seniority), stored once per job version
- [ ] Deterministic hard-constraint filtering; the LLM score only ranks jobs that pass
- [ ] Ranking with the configuration chosen in Phase 1
- [ ] `search` and `review` commands showing ranked jobs with explanations and evidence
- [ ] Ranking metrics added: NDCG@10, Precision@10, Recall@10, constraint-violation rate, extraction accuracy

**Possible experiment:** with extraction stored, test a two-step pipeline (extract, then score) against the one-call scorer from Phase 1 instead of assuming it's better.

**Done when**
- [ ] An on-demand search returns a filtered, ranked shortlist you can review in the CLI

---

## Phase 3: Scheduled runs and the evaluation loop

**Goal:** the tool searches on its own, and your decisions keep improving model and prompt choice.

**Decide before starting**
- [ ] Run cadence, maximum jobs per run, and spend cap per run

**Deliverables**
- [ ] Scheduled runs through the local OS scheduler (cron or systemd timer) with an explicit timezone
- [ ] Run IDs and persisted run status (started, finished, failed, partial); last successful run recorded; missed runs detected
- [ ] Spend cap, job limit, and concurrency limit enforced from config
- [ ] Your accept/reject decisions and reasons stored append-only, linked to the exact job version and prediction
- [ ] Decisions flow into the evaluation set
- [ ] Development/held-out split; tuning on development only, confirmation on held-out
- [ ] Error analysis: failures categorized and turned into regression tests or evaluation cases
- [ ] Explicit promotion step for a new model or prompt; nothing switches automatically

**Done when**
- [ ] A scheduled run finds and ranks jobs without you starting it
- [ ] Reviewing those jobs grows the evaluation set
- [ ] Re-running the comparison on the larger set can justify promoting a new configuration

---

## Phase 4: Evidence-backed resume tailoring

**Goal:** a resume draft tailored to one job that contains nothing you didn't actually do.

**Deliverables**
- [ ] Clarifying questions when a needed fact is missing; your answers stored as user-confirmed facts
- [ ] Claim ledger linking every substantive claim to resume text or a confirmed fact
- [ ] Deterministic claim checks (numbers, dates, technologies, employers), plus an LLM claim check that sees only the draft and the facts
- [ ] Review flow: your edits kept separate from model output; unsupported claims flagged, never marked verified
- [ ] DOCX export after explicit approval; source resume never overwritten
- [ ] Resume metrics: unsupported-claim rate, requirement coverage, semantic preservation, edits and rejections, cost per session, time to approval; human audit of a sample

**Done when**
- [ ] You can pick a job, answer questions, review claims, and export an approved draft

---

## Phase 5: Hardening

**Goal:** trust scheduled runs to work when you're not watching.

**Decide before starting**
- [ ] Where backups go and how often
- [ ] How missed-run and failure alerts reach you

**Deliverables**
- [ ] Backups through SQLite's online backup; restore documented and tested
- [ ] Missed-run and failure alerts
- [ ] Spend caps verified end to end
- [ ] Adversarial tests: prompt injection in job text, SSRF, malformed descriptions, invalid documents
- [ ] CI runs tests, type checks, linting, and evaluation regression checks

**Done when**
- [ ] A week of unattended runs completes, or every failure is reported to you

---

## Phase 6: Local web UI

**Goal:** the same workflows in a browser.

**Deliverables**
- [ ] FastAPI layer over the existing services, bound to localhost only
- [ ] React + TypeScript frontend for labeling, reviewing jobs, viewing reports, and resume review
- [ ] Labeling screen still hides model scores until you've scored a job

**Done when**
- [ ] Everything you do in the CLI can be done in the browser

If the tool is ever hosted or shared, authentication and per-user authorization come first. That's outside this roadmap.

---

## Phase 7: Work authorization and sponsorship

**Goal:** match jobs on sponsorship requirements.

**Deliverables**
- [ ] Authorization preferences
- [ ] Sponsorship extraction from job descriptions, including "unclear" when a posting doesn't say
- [ ] Filtering and ranking that use it, plus extraction-accuracy metrics
- [ ] Authorization details kept out of logs

**Done when**
- [ ] Search results reflect sponsorship requirements

---

## Ongoing across all phases

- **Evaluation:** rerun the comparison whenever a prompt, model, rubric, or the evaluation set changes.
- **Cost:** track tokens and estimated cost per job, run, and tailoring session.
- **Privacy:** only LLM provider calls and permitted job sources receive data from your machine.
- **Decision log:** record model, prompt, scoring, and architecture choices with the alternatives considered.

## Open decisions, by when they're needed

| Needed by | Decision |
|---|---|
| Phase 1 | Rubric anchor wording |
| Phase 1 | Candidate models and experiment budget |
| Phase 1 | In-house adapters or a multi-provider library |
| Phase 1 | Which job pages the URL fetcher can retrieve reliably |
| Phase 2 | First job sources |
| Phase 3 | Cadence, jobs per run, spend cap |
| Phase 3 | Target values for ranking, cost, and reliability, once baselines exist |
| Phase 4 | Resume layouts and formats beyond DOCX |
| Phase 5 | Backup location and frequency; alert channel |
| All | Dates and effort estimates |

## Changes from the original plan

- **New order:** the LLM fit-scoring spike moved first; a deterministic matcher is no longer built before the AI part. Work authorization stays last.
- **Local only:** SQLite replaced PostgreSQL. Hosting, authentication, and multi-user work were dropped, and Phase 5 became hardening for unattended runs.
- **Scoring:** a 1–5 anchored rubric, separate fit and would-apply labels, requirement-level output with quoted evidence, and blind labeling.
- **Models:** cloud and local models are both candidates; a local model is kept only if it agrees with you about as well.
- **Workflows, not agents:** finding, scoring, and tailoring are fixed workflow steps with separate prompts, not agents.