# Tech Stack Decisions

Why each tool in the job search tool was chosen, what was considered instead, and when to revisit. This is part of the decision log; `scoping.md` remains the source of truth for scope and design.

**Context for every decision:** one user, all data on their own machine, a CLI first, and production quality without over-engineering. The LLM fit-scoring spike (Phase 1) is the main unknown, so tools are picked to make experiments easy to run and compare.

**Status key:** *Decided* = agreed in planning. *Proposed* = recommended, not yet confirmed. *TBD* = still open.

## Summary

| Area | Choice | Status |
|---|---|---|
| Language | Python, with type hints | Decided |
| CLI | Typer + Rich | Decided |
| LLM output validation | Pydantic | Decided |
| Database | SQLite (one local file, WAL mode) | Decided |
| Database access and migrations | SQLAlchemy + Alembic | Proposed |
| Embeddings (if ever needed) | Vectors in SQLite + NumPy; sqlite-vec if that gets slow | Proposed |
| LLM providers | Provider-agnostic interface; cloud and local models both allowed | Decided |
| Provider adapters | Thin in-house adapters or a multi-provider library | TBD |
| Tests | pytest | Decided |
| Scheduling | cron or systemd timer, explicit timezone | Decided |
| CI | GitHub Actions or equivalent | Proposed |
| Logging and metrics | Structured JSON logs to a local file; run metrics in the database | Decided |
| Later API and UI (Phase 6) | FastAPI + React/TypeScript, localhost only | Proposed |

---

## Database: SQLite instead of PostgreSQL

**Decision:** SQLite, one local file, accessed through SQLAlchemy with Alembic migrations. *Status: decided (SQLAlchemy and Alembic proposed).*

**History:** PostgreSQL was the first choice, when the plan assumed a production system for a few users with managed hosting. Once the scope changed to one user with all data kept local, the reasons for Postgres no longer applied.

### Why PostgreSQL was originally chosen

- **Several concurrent writers:** scheduled runs, CLI sessions, and a web API could all write at once for multiple users.
- **Managed hosting:** automated backups and point-in-time restore are standard on managed Postgres.
- **JSONB:** store and index semi-structured LLM output.
- **Robust constraints and upserts** for idempotent imports with many processes writing.
- **Growth path:** built-in full-text search, and pgvector for embeddings in the same database.

### Why SQLite fits better now

| Concern | PostgreSQL | SQLite | Matters here? |
|---|---|---|---|
| Setup | A server (Docker locally, or managed) | A file; nothing to run | SQLite wins: no container to start before each run |
| Concurrent writers | Many | One at a time; WAL mode lets readers continue during a write | No: one user, with only occasional overlap between a scheduled run and a CLI session |
| Hosting and backups | Managed backups and point-in-time restore | Copy the file with SQLite's online backup | No hosting, so a file backup is enough |
| Semi-structured data | JSONB | JSON functions | Both are sufficient |
| Idempotent writes | Unique constraints, `INSERT ... ON CONFLICT` | Same | Both are sufficient |
| Full-text search | Built in | FTS5 | Both are sufficient |
| Embeddings | pgvector | sqlite-vec extension, or vectors in a table with NumPy | Both are sufficient at this scale |
| Privacy | Data lives in a server process | Data is one file on your machine | SQLite is simpler: nothing runs as a service |

A database server would add setup and moving parts without solving a problem this project has.

### Setup rules that make SQLite safe here

- **WAL mode and a busy timeout on every connection:** readers don't block the writer, and an occasional second writer waits briefly instead of failing.
- **The database file lives outside the repository** and is never committed.
- **Backups use SQLite's online backup,** which is safe while the app is running. A plain file copy mid-write can produce a corrupt backup.

### Keeping the switch cheap

- All access goes through SQLAlchemy.
- Schema changes go through Alembic migrations.
- Avoid SQLite-only SQL unless it's needed.

With those in place, moving to PostgreSQL is mostly a connection-string change plus a data migration.

### Revisit when

- The tool is hosted or shared with other users.
- Several processes need to write at the same time, regularly.
- Managed backups or point-in-time restore become requirements.

---

## Embeddings: no vector database

**Decision:** none are needed yet. If they become useful, store vectors in SQLite and compute cosine similarity with NumPy; move to the sqlite-vec extension only if that becomes slow. *Status: proposed.*

**Why:**
- The plan has no RAG step; job and resume data are stored as structured records and passed directly to prompts.
- For a single user's job list, even thousands of jobs, a brute-force similarity comparison in NumPy is fast enough and adds no dependency.
- sqlite-vec adds vector tables and nearest-neighbor queries inside SQLite. It is pre-1.0 and warns about breaking changes, so pin its version if adopted.

**Rejected:** a separate vector database. It adds a service to run and keep in sync, with no measured need.

**Revisit when:** a measured retrieval test set shows that keyword search (FTS5) isn't good enough.

---

## Language: Python

**Decision:** Python with type hints throughout. *Status: decided.*

**Why:**
- The LLM provider SDKs, local-model runtimes, and evaluation tools (NumPy, statistics libraries) are mature in Python.
- Pydantic and pytest, both central to the plan, are Python tools.
- It is already in your core stack.

---

## CLI: Typer + Rich

**Decision:** Typer for commands, Rich for output. *Status: decided.*

**Why:**
- CLI-first gets a usable tool quickly and defers frontend work until the core workflow is proven.
- Typer builds commands from type-hinted functions, which fits the typed codebase.
- Rich makes ranked job lists, comparison reports, and evidence tables readable in a terminal.
- Keeping `cli.py` thin over shared services lets the Phase 6 API reuse the same code.

---

## LLM output validation: Pydantic

**Decision:** every structured LLM output is a Pydantic model; invalid output is rejected and counted. *Status: decided.*

**Why:**
- Structural checks happen in code instead of asking the model to certify itself.
- Schema-validity rate becomes a measurable metric in every experiment.
- The same models describe data shared with the database layer and, later, the API.

---

## LLM providers: one interface, cloud and local both allowed

**Decision:** all model calls go through a provider-agnostic interface: prompt and inputs in, validated output out. Model IDs come from config. Cloud and locally run models are both candidates. *Status: decided.*

**Why:**
- Phase 1 compares several models on the same jobs. Swapping a model must be a config change plus at most one adapter, without touching scoring or evaluation code.
- A local model keeps resume text on your machine and has no per-call cost. It is kept only if its agreement with your scores is competitive with the cloud models.
- Before sending resume text to a cloud provider, check its API data-retention and training-use settings.

**Open (TBD):** thin in-house adapters or a multi-provider library. Record the choice here once made.

---

## Workflows instead of agents

**Decision:** finding, scoring, and tailoring are fixed workflow steps with separate prompts, not autonomous agents. No CrewAI, LangGraph, or multi-agent framework. *Status: decided.*

**Why:**
- The steps are known in advance: fetch jobs, score them, show them to you. An agent's main value is deciding its own next step, which isn't needed.
- Fixed steps keep runs reproducible, so evaluation measures the pipeline rather than what an agent happened to do on a given run.
- Agents multiply token cost and add failure points at handoffs.

**Rejected:** the earlier CrewAI-based version of this tool, for the reasons above.

**Revisit when:**
- **Career sites can't be adapted for:** sites vary too much to write an adapter for each. A permission-isolated agent that only returns job text could be tested, but it must never see your resume.
- **Context limits:** work splits into independent pieces too large for one context window.

---

## Testing: pytest

**Decision:** pytest for unit, integration, adversarial, and evaluation tests. *Status: decided.*

**Why:** one runner covers code tests and the evaluation harness, and evaluation regressions can run in CI next to unit tests.

---

## Scheduling: OS scheduler

**Decision:** cron or a systemd timer on Linux, or the OS equivalent elsewhere, with an explicit timezone. *Status: decided.*

**Why:**
- A periodic local job needs no extra service.
- Workflow orchestrators (such as Airflow) are built for many pipelines across machines, which this project doesn't have.

**Known limitation:** runs happen only while the machine is on, so missed runs are detected and reported.

---

## Logging and monitoring: local only

**Decision:** structured JSON logs to a local file; run status and metrics stored in the database. *Status: decided.*

**Why:**
- Enough visibility for one user, without sending personal data to an external service.
- Run history in the database makes "last successful run" and missed-run detection simple queries.

**Rejected:**
- **Sentry:** it was proposed when the plan assumed hosting; error data would leave the machine.
- **OpenTelemetry tracing:** too much infrastructure for a single local process.

Either can be revisited if the tool is ever hosted, as long as personal data stays out.

---

## CI: GitHub Actions or equivalent

**Decision:** CI runs tests, type checks, linting, and evaluation regression checks. *Status: proposed.*

**Why:** catches regressions automatically, including drops in scoring quality from prompt changes. CI uses synthetic fixtures only; no real resume or database file is ever committed.

---

## Later: FastAPI + React/TypeScript (Phase 6)

**Decision:** a FastAPI layer over the existing services, with a React + TypeScript frontend, bound to localhost. *Status: proposed.*

**Why:**
- FastAPI is Python and uses Pydantic models, so it can reuse the services and data models directly.
- The UI comes only after the CLI workflow works, so it wraps proven behavior.

**Note:** if the tool is ever hosted or shared, authentication and per-user authorization must come first.

---

## Not used, and why

| Tool | Why not |
|---|---|
| PostgreSQL | One local user; see the database section |
| Docker / Docker Compose | Its only job was running Postgres locally |
| Hosting, authentication, multi-user isolation | Everything runs on your machine |
| Vector database, RAG | No retrieval need yet; SQLite covers embeddings if one appears |
| CrewAI, LangGraph, multi-agent frameworks | Workflow steps are known in advance |
| Kafka, Kubernetes, microservices | No scale or distribution need |
| Sentry, OpenTelemetry | Local logs and run records are enough; avoids sending data off the machine |
| Fine-tuning | No baseline or labeled dataset yet to justify it |

## Sources

- [sqlite-vec on GitHub](https://github.com/asg017/sqlite-vec)