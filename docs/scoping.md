# AI Engineering Project Scoping Document

**AI-Powered Job Search + Evidence-Backed Resume Tailoring**

> **Document status:** Completed working draft based on the project discussion. Proposed design choices are labeled as such. Unknown budgets, dates, providers, and measured results remain **TBD**.

**Product priority:**
1. LLM fit-scoring spike: ingest jobs from URLs, score them against the resume with swappable LLMs, and compare each model to the user's own scores
2. Job discovery and search
3. Scheduled runs and the feedback/evaluation loop
4. Job-specific resume tailoring with claim validation
5. Hardening and a local web UI
6. Work-authorization / sponsorship matching (final feature)

**Initial delivery mode:** Terminal CLI.
**Target operating scale:** One user, all data local. Modular monolith with an embedded SQLite database; no database server, hosting, or distributed system.
**Implementation status:** Architecture and implementation sequence have been discussed. Code status and target dates have not been provided.

---

## 0. Project Summary

| Field | Completed draft |
|---|---|
| **Problem** | Job seekers need to find relevant openings across job sources, assess fit against their resume and preferences, and spend less time reviewing unsuitable roles. Tailoring a resume to an individual posting is a related but secondary workflow. |
| **Why it matters / impact** | Reduce time spent finding and assessing roles; provide an explainable shortlist; preserve user decisions as evaluation data; help users present real experience more effectively without inventing claims. |
| **Proposed approach** | Python modular monolith with a CLI-first workflow, running locally with SQLite persistence. Start with the riskiest part: an LLM fit scorer behind a provider-agnostic interface, evaluated against the user's own scores on jobs ingested from URLs. Then add job discovery, then scheduled runs with a feedback loop that grows the evaluation set and drives model and prompt selection, then evidence-backed resume tailoring. Deterministic code handles hard constraints, persistence, validation, and workflow control. Add a web UI after the core workflow works. |
| **Primary success metric** | Phase 1: agreement between each model's fit score and the user's fit score on the same 1–5 rubric (mean absolute error, within-one agreement, Spearman rank correlation, with bootstrap confidence intervals), compared with a simple non-LLM baseline. Once discovery produces ranked lists (Phase 2 onward): NDCG@10 on a versioned, human-labeled set, plus Precision@10, constraint-violation rate, and user review outcomes. Target values are TBD until a baseline is measured. |
| **Key risks / unknowns** | Availability and permissions of job sources; quality and size of labels; current model cost/quality; job description gaps; resume parsing variance; unsupported claims in generated resumes; privacy of resume data sent to hosted LLM providers. Budget, timeline, and service-level targets remain TBD. |

---

## 1. Problem Framing & Success Metrics

### Business / User Problem

**Primary user:** a job seeker who provides a resume and search preferences and wants the tool to discover, rank, and explain promising job openings. Users may *optionally* provide URLs of positions they have already applied to; these become an initial labeled evaluation set after the user reviews and labels their fit.

The workflow also supports tailored resume drafts for selected jobs. The tool should ask the user to clarify missing facts and make material edits reviewable. It must not invent skills, responsibilities, metrics, dates, ownership, or accomplishments.

AI is useful for extracting requirements from unstructured job descriptions, comparing semantically related experience, and drafting phrasing. Deterministic software is preferable for explicit preference filters, score calculation, persistence, validation, and workflow control. The project is **not** planned as an autonomous agent.

### Goals

- First, prove the core AI capability: ingest jobs from URLs, score fit against the resume with several LLMs, and measure each model against the user's own scores. Models must be swappable without changing scoring or evaluation code.
- Then provide a CLI job-search workflow: configure preferences, discover listings, filter, rank, and review.
- Run discovery on a user-configured cadence, give the user final decision authority, and feed those decisions back into the evaluation set to guide model and prompt selection.
- After job search is usable, offer position-specific resume tailoring with traceable evidence and user approval.
- Add work-authorization and sponsorship matching only in the final implementation phase.

Measurable targets and delivery dates: **TBD**. Establish baselines before setting numeric thresholds; do not claim model quality until experiments have run.

### Stakeholders

| Stakeholder | Role / ownership | Status |
|---|---|---|
| End user / job seeker | Provides resume, preferences, historical job URLs, fit labels, corrections, and final decisions. | Defined product role; names TBD. |
| Project owner / developer | Owns architecture, implementation, evaluation, deployment, and maintenance. | Specific assignment not provided. |
| External job sources | Provide listings through APIs, feeds, or other permitted access methods. | Sources TBD; verify terms before integration. |
| LLM provider(s) | Provide structured extraction and generation APIs. | Cloud and locally run models are both acceptable. Specific providers TBD. |
| Hosting / database provider | Not applicable: the tool and its SQLite database run on the user's own machine. | No hosting planned. |
| Security / legal reviewer | Reviews privacy, data handling, and source-use questions if required. | No named stakeholder or completed review provided. |

### Prior Work

The plan was revised to start with LLM fit scoring evaluated against human scores, instead of building a deterministic matcher first. A simple non-LLM baseline (proposed: keyword/skill overlap between job and resume) is kept in every comparison so LLM results are judged against something cheap. Existing applied-position URLs are intended as the seed evaluation set. No external product audit, competitor research, or prior implementation results have been provided.

### Input and Output

| Input / data | Output / persistence |
|---|---|
| Optional historical applied-job URLs and any available job metadata/descriptions | Normalized job records, job-description versions, application status, and user-reviewed fit labels. |
| User's own labels per job: fit score (1–5, same rubric the LLM uses) and would-apply (yes/no) | Human labels stored separately from model scores. The fit score is the reference for model comparison; would-apply informs later preferences and ranking. |
| Resume file (initially text-based PDF/DOCX where parsable) and parsed text | Original resume reference, versioned normalized profile, and evidence-backed facts for the later resume-tailoring phase. |
| User preferences: roles, seniority, countries/regions/cities, work arrangement (e.g., remote/hybrid), required vs. preferred constraints | Persistent profile/preferences used by deterministic filtering and ranking. Work-authorization information is deferred to the final phase. |
| Configured job sources / feed data | New or updated job listings, source metadata, timestamps, and run-level outcomes. |
| LLM outputs and user feedback | Validated requirement structures, scores and explanations, claim ledgers, immutable feedback events, and experiment results. |
| User-selected job for tailoring, answers to follow-up questions, edits and approvals | Versioned job-specific resume draft and approved export; each substantive claim references supporting facts. |

### Constraints

| Constraint | Target | Notes |
|---|---|---|
| Latency (p50 / p99) | TBD; measure baseline first. | Scheduled job search is a batch workflow; no strict interactive latency target was specified. Track per-stage and end-to-end latency. |
| Cost per request / run | TBD; set budget after model comparison. | Track per-job analysis cost, cost per 100 jobs, cost per tailoring session, and total run cost. Add configurable spend limits. |
| Quality bar | TBD numeric thresholds. | Measure ranking quality and extraction accuracy. Unsupported resume claims must not silently pass as verified; preserve human approval. |
| Privacy / compliance | All data is stored on the local machine; no resume text or secrets in routine logs. | Storage is local, but cloud LLM requests may send job and resume text to the provider, subject to checking that provider's documented API data-retention and training-use settings first. Locally run models keep that data on the machine. Requests to job sources are the only other outbound traffic. |
| Availability / uptime | Not applicable as a service. | Runs only while the local machine is on. Reliable scheduled runs, detection of missed runs, recoverable failures, backups, and visible run status are what matter. |
| Scale / complexity | One local user; single modular monolith with SQLite. | No database server, Kafka, Kubernetes, or microservices. Add infrastructure only when measured needs justify it. |
| Job source constraints | Use APIs, feeds, or sites where automated access is permitted. | Source availability, terms, rate limits, and data freshness are unknown and must be validated per integration. |

### Success Metrics

| Level | Metrics to track | Initial measurement plan |
|---|---|---|
| User-facing | Relevant jobs in top results; feedback completion; time spent reviewing jobs; resume draft acceptance/edit rate; time to resume approval. | Instrument CLI review decisions. Establish baseline before setting targets. |
| Technical — fit scoring (Phase 1) | Mean absolute error, within-one agreement, mean signed error, Spearman rank correlation, confusion matrix, run-to-run variance, schema-validity rate, fabricated-quote rate, cost and latency per job. | Label blind, before model output exists. Cover the full 1–5 range. Report bootstrap confidence intervals; the first set will be small. See the rubric in Section 2. |
| Technical — job search (Phase 2 onward) | NDCG@10, Precision@10, Recall@10, location/work-arrangement extraction accuracy, constraint-violation rate, schema-validity rate. | Evaluate on fixed labeled cases; audit errors and use appropriate cross-validation when data is small. |
| Technical — resume tailoring | Unsupported-claim rate, claim verification precision on audited samples, semantic preservation, requirement coverage, unresolved-claim count. | Human-audit a representative sample. Do not rely on an LLM judge alone. |
| System | Run success/failure, source failures, latency, model cost, retries, database errors, export failures, last successful run. | Structured logs, run IDs, metrics, and error reporting. |

### Checklist / current planning status

- [ ] The problem and proposed user outcome are documented; importance has not yet been validated with external users. *(Planning item; completion not verified)*
- [ ] AI is planned for unstructured requirement extraction and drafting; use of AI versus heuristics should be assessed through the evaluation harness. *(Planning item; completion not verified)*
- [ ] Measurable goals are proposed, but numeric thresholds and timelines remain TBD. *(Planning item; completion not verified)*
- [ ] No other stakeholders, source contracts, or legal/security reviews have been confirmed. *(Planning item; completion not verified)*
- [ ] Record trade-offs, experiments, source constraints, and model/prompt decisions in README and decision log. *(Planning item; completion not verified)*

---

## 2. Prompt Engineering & Systematic Tracking

### Prompt Architecture

Prompts should be task-specific rather than one general prompt. Expected prompt groups (proposed; exact implementation may change):

- **Job requirement extraction:** transform a job description into validated structured fields such as required/preferred skills, responsibilities, seniority, locations, and work arrangement. Sponsorship extraction is deferred until the final feature phase.
- **Job-to-resume fit scoring:** for each job requirement, return a status (met, partial, not met, unclear) with a quoted resume line as evidence, then an overall 1–5 score on the shared rubric. In Phase 1 this score is compared with the user's score. In search, hard constraints stay deterministic and the score only ranks eligible jobs.
- **Job-fit explanation:** summarize why a job matched or did not match, grounded in scored evidence.
- **Resume tailoring:** select and rewrite only supported experience for a particular job; return structured draft sections and claim-to-fact references.
- **Claim validation:** flag potentially unsupported metrics, dates, technologies, scope, ownership, or accomplishments. Treat this as a secondary detector, not proof of truth.
- **Output validation/parsing:** use Pydantic and deterministic application checks for structure and required fields rather than asking the model to self-certify correctness.

**Few-shot examples:** add only when an evaluation shows they improve a failure pattern. No fixed number of prompts or examples has been chosen.

### Prompt Organization

Store prompts as versioned files, not hardcoded strings. Capture the prompt version with every prediction and experiment. Proposed layout:

```
prompts/job_requirements/extract_v1.txt
prompts/job_matching/fit_score_v1.txt
prompts/job_matching/explanation_v1.txt
prompts/resume_tailoring/draft_v1.txt
prompts/resume_tailoring/claim_review_v1.txt
prompts/examples/            # versioned JSON examples, if useful
```

Record changes and rationale in a prompt changelog or decision log. Do not use held-out test data to tune prompts.

### Fit-Scoring Rubric (Phase 1, draft)

The user and every model score each job against the same written rubric. Finalize the wording before labeling starts, then version it like a prompt (proposed: `prompts/job_matching/rubric_v1.md`). Changing the rubric means re-labeling or keeping results separate by rubric version.

| Score | Meaning |
|---|---|
| 5 | Meets all required qualifications and most preferred ones; seniority matches. |
| 4 | Meets all or nearly all required qualifications; gaps only in preferred ones. |
| 3 | Meets most required qualifications, with one notable gap. |
| 2 | Several required gaps, or a clear seniority mismatch. |
| 1 | Different field or level. |

A 1–5 scale is used instead of 0–100 because neither a person nor a model can reliably tell small differences on a fine scale; the extra precision is noise.

**Two separate human labels per job:**
- **Fit score (1–5):** qualification fit only, judged from the job description and resume. Models are graded against this label.
- **Would apply (yes/no):** includes everything the model cannot see, such as pay, company, and personal interest. Never used to grade the fit scorer; it informs Phase 2 preferences and ranking.

**Model output (validated with Pydantic):**
- For each job requirement: a status (met, partial, not met, or unclear) and a quoted line from the resume as evidence. "Unclear" is a valid answer so the model is not forced to guess when a job description is vague.
- An overall 1–5 score with a short rationale.
- Code checks that every quoted evidence line appears in the resume text; an output with a fabricated quote fails validation.
- A second, deterministic score is computed in code from requirement coverage (weights in config) and compared with the model's holistic score in the same experiment.

**Labeling protocol:**
- Score each job before any model output exists for it.
- Cover the whole scale: applied-to jobs skew toward 4–5, so deliberately add jobs expected to score 1–3. Otherwise agreement looks better than it is and rank correlation is close to meaningless.
- Measure your own consistency: after a week or two, re-score a sample of about 10 jobs without looking at the earlier scores. The gap between your two passes is roughly the best agreement a model can reach.

**Comparison metrics (per model × prompt version, plus the non-LLM baseline mapped onto the same 1–5 scale):**
- Mean absolute error and within-one agreement (share of jobs where the model is at most one point off).
- Mean signed error, to show whether a model is systematically generous or harsh.
- Spearman rank correlation, since ranking is what search needs.
- Confusion matrix, since missing the 3-vs-4 boundary matters more than mixing up 1 and 2.
- Run-to-run variance on a repeated subset; schema-validity rate; fabricated-quote rate; cost and latency per job.
- Bootstrap confidence intervals on the main metrics. Models with heavily overlapping intervals are treated as tied, and the cheaper one is preferred.

**Selection order:** agreement with the user's fit scores first, then consistency across runs, then cost and latency.

### Evaluation Framework

| Aspect | Approach |
|---|---|
| Test set size | TBD. Start with the actual applied-job set and increase judged examples over time. If the dataset is small, use repeated cross-validation or leave-one-out where appropriate and report uncertainty; do not imply a tiny set proves generalization. |
| Test set composition | Previously applied positions after user labels, plus deliberately added lower-fit jobs (expected scores 1–3) so the set spans the full scale; newly discovered positive and negative examples; edge cases for missing descriptions, duplicates, unclear locations, preference conflicts, and (in the final phase) authorization-policy ambiguity. |
| Scoring rubric | The user and the LLM score on the same 1–5 anchored rubric (draft above). The rubric is versioned like a prompt. |
| Data splits | Once enough labels exist (Phase 3), split into development and held-out sets. Iterate on development; confirm on held-out. |
| Metrics | Fit scoring: agreement with human scores (proposed: mean absolute error, rank correlation) against a non-LLM baseline. Job ranking (once discovery exists): NDCG@10, Precision@10, Recall@10. Extraction: field-level accuracy / schema validity. Resume: claim support, semantic preservation, requirement coverage, and human review. Operational: cost / latency / failures. |
| Tooling | pytest plus a custom, versioned evaluation runner and stored result tables / JSON / CSV reports. Specific commercial evaluation product TBD. |
| Update cadence | Add reviewed failure cases when new patterns are found; evaluate proposed prompt changes before deployment. Cadence beyond this is TBD. |
| Human review | Record your own fit score before seeing any model score, to avoid anchoring. Re-score a sample after a delay to measure your own consistency. Audit explanations and resume claims against source evidence. LLM-as-judge may supplement review but should not be the only correctness check. |

- [ ] Prompts are stored in separate versioned files. *(Planned; not yet verified)*
- [ ] A structured test set with expected labels/criteria exists. *(Planned; not yet verified)*
- [ ] Prompt comparison can be rerun reproducibly. *(Planned; not yet verified)*
- [ ] Prompt changes are evaluated before deployment. *(Planned; not yet verified)*
- [ ] Prompt iterations and key learnings are documented. *(Planned; not yet verified)*

---

## 3. Model Selection & Evaluation

### Candidate Models

No specific provider or model has been selected. Cloud and locally run models are both acceptable candidates; a local model is worth including because it keeps resume text on the machine and has no per-call cost, and it is kept only if its agreement with the user's scores is competitive. Compare 2–3 currently available models across appropriate providers or model sizes in Phase 1. All models sit behind one provider-agnostic interface (prompt and inputs in, validated structured score out), so adding a model is a config change plus, at most, one new adapter. Whether to write thin adapters or use a multi-provider library is TBD; record the choice in the decision log. Capture current pricing and license/terms at evaluation time rather than hardcoding unverified figures.

| Candidate | Provider | Cost | Latency | Quality / license notes |
|---|---|---|---|---|
| Model A — TBD | TBD | Measure input/output cost on chosen tasks. | Measure p50/p95 or p99. | Run on the same dataset; verify structured-output support and applicable terms. |
| Model B — TBD | TBD | Measure input/output cost on chosen tasks. | Measure. | Same prompts, examples, and scoring pipeline where possible. |
| Model C — locally run (optional) | TBD | No per-call cost; note hardware and runtime requirements. | Measure on the local machine. | Include to test whether a local model is competitive; keeps all data on the machine. |

### Evaluation Approach

- Run candidates on identical labeled examples and equivalent task instructions.
- Compare job extraction and ranking quality separately from end-to-end ranking, to identify where differences originate.
- For resume tailoring, compare factual support, semantic preservation, review outcomes, and cost; inspect example-level regressions.
- Record exact provider/model ID, prompt version, input/output tokens, latency, estimated cost, schema validity, and dataset version.
- Use held-out data for final comparison; repeat a subset to characterize model variability.
- Choose by agreement with the user's fit scores first, then consistency, then cost and latency. Treat models whose confidence intervals heavily overlap as tied and prefer the cheaper one.
- Explicitly promote a selected configuration after review. Do not silently change the production model when a new model becomes available.

### Model Routing

Initial proposal: use one selected production configuration per task. Compare candidates offline. Do not add dynamic model routing at the start; revisit only if measurements show that a cheaper model can safely handle simpler cases or that fallback is needed.

**License and provider terms:** TBD pending provider selection. Verify data retention, training use, commercial-use terms, pricing, rate limits, and structured-output behavior before production use.

- [ ] At least 2–3 candidate models tested on the same dataset. *(Planned; not yet verified)*
- [ ] Performance, cost, and latency compared systematically. *(Planned; not yet verified)*
- [ ] Terms and licenses reviewed for the intended use. *(Planned; not yet verified)*
- [ ] Final model choice documented with evidence. *(Planned; not yet verified)*
- [ ] Any routing strategy documented and evaluated before use. *(Planned; not yet verified)*

---

## 4. RAG Implementation

> **Initial scope: not planned.** The described architecture does not require a conventional retrieval-augmented generation (RAG) system or vector database to deliver the first releases. Resume facts and job descriptions can be stored as structured, versioned records and passed to task-specific prompts. This section is retained to record the decision; revisit if search over a large evidence corpus becomes necessary.

### Data Sources

| Source | Type | Initial handling | Update |
|---|---|---|---|
| User-supplied resume | Personal document / parsed text | Parse to structured profile and evidence-backed facts; retain provenance and versions. | On user upload/edit. |
| Job descriptions | Text from permitted source or user input | Normalize and version; preserve source URL and retrieval timestamp. | During discovery/import; cadence TBD. |
| User feedback | Structured labels and optional notes | Store append-only and link to the exact prediction/job version. | As users review jobs and drafts. |

### Chunking, Embeddings, Vector Store and Retrieval

Not applicable to the initial plan. No chunk size, overlap, embedding model, or vector store has been selected. A vector store is not required for one user and a modest dataset. If evidence retrieval later becomes difficult, compare SQLite FTS5 full-text search, keyword/semantic hybrid search, and embeddings using a measured retrieval test set. Embeddings can start as vectors stored in SQLite with similarity computed in NumPy; the sqlite-vec extension (pre-1.0) is an option if that becomes slow. No separate vector database.

### RAG Evaluation

Not applicable until RAG is deliberately introduced. If added, create questions or job requirements with known supporting evidence locations and measure Precision@K, Recall@K, answer/evidence correctness, and end-to-end quality. Keep retrieval errors separate from generation errors.

---

## 5. Agent Systems

> **Initial scope: no agent framework.** Use explicit, bounded application workflows:
> `discover → normalize → eligibility → score → explain → persist → review`.
> No autonomous agents, tool-calling loop, LangGraph, CrewAI, or multi-agent system is planned.

### Workflow Components

| Component / tool | Purpose | Error handling |
|---|---|---|
| Job source adapter | Fetch listings from a permitted API/feed/site. | Timeout, bounded retry/backoff, per-source failure record; continue with other sources when possible. |
| LLM provider adapter | Structured requirement extraction or resume drafting. | Timeout, limited retries, schema validation, budget cap, clear failed/partial state. |
| Database repository | Persist jobs, predictions, run state, facts, feedback, and drafts. | Transactions, unique constraints, migrations, recoverable run state. |
| Document parser/exporter | Parse supported resumes and produce approved DOCX output. | Validate file type/size; report parsing/export errors; never overwrite the source resume. |
| Human-review command | Collect labels, corrections, and approvals. | Persist events; keep user edits distinct from model output. |

### Safety & Robustness

| Concern | Approach |
|---|---|
| Error handling | Bounded retries with exponential backoff and jitter for transient errors. Record permanent failures and partial completion. |
| Security | Validate all inputs; keep secrets and the database file out of source control; protect personal documents; bind any web UI to localhost; defend URL fetching against SSRF. If the tool is ever shared or hosted, add authentication and per-user authorization first. |
| Infinite loops / runaway cost | No autonomous loops initially. Configure maximum jobs per run, concurrency limits, timeouts, retry limits, and model-spend caps. |
| Guardrails | Treat job pages as untrusted text. Validate structured output. Never allow source text to execute tools or override application policy. Require user approval before resume export and never auto-apply to jobs. |
| Cost monitoring | Track tokens and estimated spend per job / run / tailoring session; alert or stop at a configured budget threshold. Threshold TBD. |

### Agent Evaluation

Not applicable as an agent system. Test source adapters and services with unit tests, complete search and tailoring flows with integration tests, and adversarial inputs such as prompt injection, malformed job descriptions, inconsistent facts, unsupported numbers, and invalid documents.

---

## 6. Deployment & User Interface

### Tech Stack Summary (proposed)

| Layer | Choice | Rationale (from discussion) |
|---|---|---|
| Language | Python | Modular monolith; Pydantic validation of LLM output; pytest-based evaluation runner. |
| CLI | Typer + Rich | CLI-first delivery gives a usable early feature and keeps the initial implementation simpler. |
| Validation | Pydantic | Structural checks on LLM output instead of asking the model to self-certify. |
| Database | SQLite (one local file, WAL mode), accessed through SQLAlchemy with Alembic migrations (proposed) | One local user and no server to run. Supports JSON, unique constraints, upserts, transactions, and full-text search. SQLAlchemy keeps a later move to PostgreSQL mostly a connection-string change if the tool is ever hosted. |
| Scheduling | cron or systemd timer (explicit timezone) | Sufficient for scheduled runs at this scale. |
| Containers | Not needed initially | No database server to run; revisit only if packaging requires it. |
| CI | GitHub Actions or equivalent | Tests, type checks, linting, evaluation regression checks. |
| Error reporting | Local log file and persisted run status | Error visibility for scheduled runs without sending personal data to an external service. |
| Metrics | Per-run counters and durations stored in the database | Enough for a local tool; no separate metrics stack. |
| Later API | FastAPI | Added when a web frontend is introduced; reuses application services. |
| Later frontend | React + TypeScript | Local web UI (localhost) after the core workflow works. |
| Not used | Database server, hosting, Kafka, Kubernetes, microservices, vector DB, agent frameworks | No demonstrated need for a single local user. |

### API Design

| Aspect | Approach |
|---|---|
| Framework | CLI first (Typer). FastAPI is proposed when a web frontend is added. |
| Streaming | Not required for the first CLI workflow. Revisit for the web UI if long-running operations benefit from streaming progress. |
| Authentication | Not needed: one local user, and the web UI binds to localhost only. If the tool is ever hosted or shared, add authentication and per-user authorization first. |
| Rate limiting | Not needed for a local API; apply per-source and per-provider limits and bounded concurrency in scheduled runs. |
| Error handling | Map provider timeouts/rate limits to explicit errors, retry transient failures within limits, retain partial run status, and show useful CLI summaries. |

### User Interface

| Aspect | Approach |
|---|---|
| Initial UI | Terminal CLI using Typer and Rich. |
| Job interactions | Ingest a job from a URL; record your own fit score; run a model comparison and view the report; configure preferences; run search; review ranked jobs and evidence; label fit; inspect run history. |
| Resume interactions (Phase 4) | Choose a job; answer factual questions; inspect evidence and suggested edits; correct or reject claims; approve/export draft. |
| Feedback mechanism | Fit labels plus optional rejection reason. Resume-specific accepted/rejected edits and factual corrections are tracked separately. |
| Later UI | React + TypeScript frontend backed by FastAPI on localhost; reuse application services and database. |

### Infrastructure

| Aspect | Approach |
|---|---|
| Hosting | None. Everything runs on the user's own machine. |
| Database | SQLite file on the local machine, outside the repository. WAL mode and a busy timeout so a scheduled run and a CLI session can overlap safely. |
| Containerization | Not required; no database server. |
| Scheduling | The local OS scheduler (cron or a systemd timer on Linux; the OS equivalent elsewhere). Runs only while the machine is on, so missed runs are detected and reported. Scheduler timezone must be explicit. |
| CI/CD | GitHub Actions or equivalent proposed for tests, type checks, linting, and evaluation regression checks. No deployment automation; installation is local. |
| Backups | Copy the database file with SQLite's online backup, which is safe while the app may be running. Backup location and frequency TBD. |
| Document storage | Parsed text and metadata in SQLite; original files stay in a local folder; the source resume is never overwritten. |

- [ ] CLI is usable with clear setup instructions. *(Planned; not yet verified)*
- [ ] API errors and rate limits have explicit handling when an API is added. *(Planned; not yet verified)*
- [ ] Web UI supports feedback and accessible review controls. *(Planned; not yet verified)*
- [ ] Local setup (install, migrate, run) is reproducible from the README. *(Planned; not yet verified)*
- [ ] CI runs tests automatically. *(Planned; not yet verified)*
- [ ] Database backup and restore are documented and tested. *(Planned; not yet verified)*

---

## 7. System Monitoring & Error Analysis

### Component-Level Monitoring

| Component | Metrics to track |
|---|---|
| Prompts / LLM | Schema validity, field extraction accuracy, human-rated quality, explanation evidence accuracy, failure/retry rate, response length, token usage, latency, cost. |
| Job discovery / sources | Sources checked, new jobs, duplicates, missing descriptions, stale listings, source failure rate, retrieval latency, last successful retrieval. |
| Matching / eligibility | NDCG@10, Precision@10, Recall@10, constraint violations, score distribution, unknown-location count. Authorization-specific metrics begin in Phase 7. |
| Resume tailoring (Phase 4) | Evidence coverage, unresolved claims, unsupported-claim audit rate, semantic preservation, user edits/rejections, export failures, cost/session, time to approval. |
| Overall system | End-to-end run success, errors, duration, user review/feedback completion, costs, database file health, last successful run, missed scheduled runs. |

### Logging Approach

| Level | Tool / method |
|---|---|
| Basic | Structured JSON logs with timestamp, run/session ID, component, model/prompt/version metadata, latency, token/cost estimates, status, and error category. |
| Error reporting | Local log file plus failed-run records in the database. An external service is optional and must not receive personal data. |
| Metrics / tracing | A small set of per-run counters and durations stored in the database. No tracing stack for a local tool. |
| Sensitive fields | Do not log full resumes, resume fact-bank contents, credentials, or unnecessary work-authorization details. Prefer redacted metadata and references. |

### Error Analysis Process

1. Review failed runs and user-reported poor matches regularly; cadence TBD.
2. Categorize errors: source/retrieval failure, parsing/normalization, missing or incorrect requirement extraction, ranking, constraint logic, hallucinated explanation, unsupported resume claim, export failure, and operational error.
3. Convert representative failures into regression tests and, when appropriate, reviewed evaluation cases.
4. Change prompts, scoring rules, or parsers only with an experiment, and compare against the existing baseline.
5. Alert on repeated run failure, model spend beyond a configured cap, unusual schema-failure rate, and severe quality regression once baselines and thresholds exist.

---

## 8. Fine-Tuning (Optional / Advanced)

> **Decision: deferred / not in planned scope.** First optimize deterministic scoring, structured prompts, evidence tracking, validation, and model selection. Fine-tuning is not justified yet because dataset size, baseline performance, and a clear fine-tuning use case are unknown.

| Aspect | Current status / future gate |
|---|---|
| Use case | TBD. Consider only if a stable task shows a persistent limitation that prompt/system improvements and model selection do not address. |
| Training data | TBD; no approved training dataset or example count provided. |
| Base model | TBD; no candidate selected. |
| Baseline | Must be measured on a held-out test set before any fine-tuning experiment. |
| Versions / comparison | Only experiment with data sizes/hyperparameters if a concrete hypothesis and sufficient high-quality labels exist. |
| Decision criterion | Keep fine-tuning only if it provides a measured improvement in quality, cost, or latency without unacceptable regression. |

---

## 9. Code Quality & Repository Structure

### Proposed Project Structure

```
job-search/
├── pyproject.toml
├── README.md
├── .env.example
├── config/
│   └── config.example.yaml
├── migrations/
├── prompts/
│   ├── job_requirements/
│   ├── job_matching/
│   └── resume_tailoring/
├── src/jobsearch/
│   ├── cli.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   ├── services/
│   │   ├── profiles.py
│   │   ├── ingestion.py
│   │   ├── discovery.py
│   │   ├── scoring.py
│   │   ├── matching.py
│   │   ├── feedback.py
│   │   ├── resume_tailoring.py
│   │   └── runs.py
│   ├── llm/providers/
│   ├── evaluation/
│   └── observability/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── evaluation/
├── api/              # Phase 6
├── frontend/         # Phase 6
├── deployment/       # local scheduler setup (cron/systemd)
└── docs/             # diagrams and decision log
```

### Code Standards

- Type hints on functions and clear docstrings for public interfaces.
- No hardcoded secrets, model IDs, schedules, scoring weights, or spend limits; use typed configuration and environment/secrets management.
- Modular Python modules with deterministic services; avoid monolithic notebooks and avoid introducing agents without a demonstrated need.
- Unit tests for scoring, eligibility, data normalization, claim validation, and parsers; integration tests for database workflows and complete runs.
- Database migrations checked into source control; unique constraints and idempotent writes for repeatable imports/runs.
- Prompt files and evaluation datasets/results are versioned; private resumes, personal data, and the database file are not committed to the repository.

### README Contents

- Project overview and scope priorities.
- Architecture diagram and module responsibilities.
- Local setup, configuration, database migration, and example CLI commands.
- Job source constraints and provider setup; no secrets in repository.
- Evaluation methodology and measured results (once available).
- Security/privacy notes (including what data leaves the machine), backup/restore, error handling, and scheduler setup.
- Decision log including alternatives considered and rationale.

---

## 10. Project Timeline & Milestones

No target dates or implementation status were supplied. The sequence below reflects the latest agreed priority order; each phase is intended to leave a usable feature. Target dates should be assigned after estimating effort and defining the first source integration.

| Phase | Milestone | Scope | Usable outcome | Relative priority | Target date | Status |
|---|---|---|---|---|---|---|
| 1 | LLM fit-scoring spike and model comparison | Project setup, SQLite, migrations. CLI command to ingest a job from a URL into a normalized, versioned record (proposed: paste-text fallback for pages that can't be fetched). Load the resume (text-based PDF/DOCX). Record your own fit score (1–5) and would-apply label per job on the shared rubric, blind to model output. Provider-agnostic LLM scorer (cloud and local candidates) returning requirement-level statuses with quoted resume evidence, a 1–5 score, and a rationale; quotes are checked against the resume in code. Experiment runner over models × prompt versions × jobs, including a non-LLM baseline; reports agreement with your scores, variance, cost, latency, and schema validity. | Add jobs you've applied to, score them yourself, and see which model and prompt agree with you most, and at what cost. | Core first; main spike | TBD | Planned |
| 2 | Job discovery and search | Job source adapters (permitted sources only). Preferences: roles, seniority, country/city, remote/hybrid/onsite, required vs. preferred. Deterministic hard-constraint filtering, deduplication, ranking with the selected scoring configuration, CLI review with explanations. | Run a search on demand and review a ranked, filtered shortlist. | Core | TBD | Planned |
| 3 | Scheduled runs and feedback/evaluation loop | Runs at a user-configured cadence (cron/systemd) with run status and failure records. Your accept/reject decisions and reasons stored append-only and added to the evaluation set. Development/held-out split, error analysis, ranking metrics (NDCG@10 etc.). Prompt and model changes tested on development data, confirmed on held-out, and promoted explicitly. | The tool finds jobs on its own, and every decision you make grows the evaluation set and informs which model and prompt to use. | Primary product milestone | TBD | Planned |
| 4 | Evidence-backed resume tailoring | Clarifying questions, claim ledger, claim validation, user review/approval, DOCX export. | Get a tailored, evidence-checked resume draft for a chosen job. | Second major feature | TBD | Planned |
| 5 | Hardening | Tested backup/restore, missed-run and failure alerts (mechanism TBD), spend caps, CI, adversarial tests (prompt injection, SSRF, malformed input). | Scheduled runs can be left unattended. | Before relying on unattended runs | TBD | Planned |
| 6 | Web frontend (FastAPI + React/TypeScript) | Local web UI on localhost, reusing application services and database. | Use the tool in a browser. | Later UI | TBD | Planned / optional timing |
| 7 | Work authorization and sponsorship matching | Authorization preferences, sponsorship extraction, and related metrics. | Filter and rank by sponsorship requirements. | Final feature | TBD | Explicitly deferred to last phase |

**Evaluation is continuous:** model and prompt comparison starts in Phase 1 and becomes a recurring loop in Phase 3, rerun whenever a prompt, model, or the evaluation set changes. Resume-specific evaluation is added in Phase 4.

---

## 11. Appendix

### Architecture Summary

```
CLI / scheduled runner
        |
Application services:
  profiles | ingestion | scoring (LLM provider interface) | evaluation
  | discovery | location/work-arrangement filters | matching | feedback
  | resume_tailoring (Phase 4) | authorization (Phase 7)
        |
SQLite (local file): preferences, resumes, jobs, versions, scores,
                    runs, feedback, experiments
        |
External calls: permitted job sources; LLM provider(s)
```

**Priority order:** LLM fit-scoring spike → job search → scheduled runs and feedback/evaluation loop → resume tailoring → hardening / local web UI → work authorization last.

### Glossary

| Term | Meaning in this project |
|---|---|
| NDCG@10 | Ranking metric that rewards putting more relevant jobs near the top, using graded relevance labels. |
| Score agreement | How closely a model's fit score matches the user's score for the same job. Mean absolute error is the average gap; within-one agreement is the share of jobs at most one point off; mean signed error shows systematic generosity or harshness; Spearman rank correlation checks whether both order jobs the same way. |
| Fit score vs. would apply | Fit score (1–5) rates qualification fit only and is what models are graded on. Would apply (yes/no) captures the user's overall interest, including factors the model cannot see. |
| Bootstrap confidence interval | A range for a metric estimated by resampling the labeled jobs many times; shows how much a result could change with a different sample. |
| Precision@10 / Recall@10 | Precision: share of the top 10 that are relevant. Recall: share of known relevant evaluation jobs appearing in the top 10. |
| Hard constraint | User requirement that excludes a known-incompatible job from the eligible shortlist. |
| Soft preference | Preference that affects ranking but does not automatically exclude a job. |
| Evidence-backed claim | Resume statement connected to source resume text or explicit user-confirmed information. |
| Claim ledger | Record that links each substantive draft claim to evidence, validation state, and review/approval status. |
| Idempotency | Re-running the same import or operation does not create duplicate logical records or side effects. |
| RAG | Retrieval-augmented generation. Not planned in the initial architecture. |
| Agent system | Autonomous tool-using loop. Not planned; use explicit bounded workflows instead. |

### Decision Log

| Decision | Rationale / current status |
|---|---|
| CLI first | Provides a usable early feature and keeps initial implementation simpler. Frontend comes later. |
| Modular monolith | Appropriate for a single local user; easier to test and run than microservices. Revisit only if needed. |
| Local, single-user deployment | All data stays on the user's machine; no hosting, authentication, or multi-user isolation needed. Revisit if the tool is ever shared. |
| SQLite for persistence (replaces the earlier PostgreSQL choice) | One user at a time and all data local, so a database server adds setup without benefit. SQLite covers JSON, constraints, upserts, transactions, and full-text search, and backup is a file copy. Access through SQLAlchemy and Alembic keeps a move to PostgreSQL cheap if needs change. |
| LLM fit-scoring spike first | The LLM part is the main unknown; validating it against human scores early shows whether the approach works before building discovery around it, and gives a working result quickly. |
| Provider-agnostic LLM interface | Lets models be swapped and compared without changing scoring or evaluation code. |
| Keep a non-LLM baseline in every comparison | Shows whether an LLM is worth its cost; hard constraints stay deterministic and understandable. |
| Human scores recorded before model scores | Avoids anchoring the reference labels on model output. |
| 1–5 anchored rubric instead of 0–100 | Finer scales add noise rather than information; written anchors make human and model scores comparable. |
| Separate fit score and would-apply labels | The model only sees the job and resume, so it is graded on qualification fit; interest factors it cannot see are recorded separately. |
| Requirement-level output with quoted evidence | Quotes can be checked against the resume in code, catching fabricated evidence, and requirement coverage gives a second, deterministic score to compare against. |
| Cloud and local models both acceptable | Resume text may be sent to cloud providers after checking their data settings; a local model is included as a candidate and kept only if it is competitive. |
| LLM for unstructured extraction / drafting, not source of truth | LLM responses are validated; database facts, explicit constraints, and user decisions remain authoritative. |
| Applied jobs are initial evaluation examples, not automatic ground truth | User should label fit; application itself is only a weak positive signal. |
| Feedback first updates evaluation data | Avoids uncontrolled self-reinforcing behavior; proposed changes are measured offline before promotion. |
| Resume tailoring after job search and the feedback loop | Build after end-to-end job discovery and feedback; must include evidence mapping, clarifying questions, validation, and explicit user approval. |
| Work authorization last | Explicitly deferred until after the rest of the planned product; location/work-arrangement preferences come earlier. |
| No RAG, agents, or fine-tuning initially | No demonstrated need yet; revisit only if evaluation and scale justify the added complexity. |
| Numeric targets, providers, budget, dates TBD | Not specified in discussion; must be chosen/measured rather than invented. |

### Open Decisions / Unknowns

- Final wording of the 1–5 rubric anchors (draft in Section 2), to settle before labeling starts.
- Which job pages can the Phase 1 URL fetcher retrieve reliably, and is a paste-text fallback needed?
- Thin in-house provider adapters or a multi-provider library?
- Which job sources are permitted, reliable, and worth integrating first?
- Which LLM providers/models should be evaluated, and what is the spending budget?
- What target values should be set for NDCG@10, latency, cost, and operational reliability after the baseline is measured?
- How many resumes and jobs per scheduled run should the tool support?
- Where should database backups go, and how often?
- How should missed-run and failure alerts reach the user?
- What document layouts and output formats are required beyond initial DOCX export?
- What target dates and effort estimates should be assigned to each phase?

External links, production dashboards, evaluation results, and repository URL: **not provided yet.**