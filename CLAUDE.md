# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

## Current state

Planning only. There is no code yet, and no build, lint, or test tooling. `data/` and `README.md` are empty. Don't invent commands. Once `pyproject.toml` and the rest of the tooling exist, fill in the Commands section of `AGENTS.md`, including how to run a single test.

## Where the design lives

- `docs/scoping.md` is the source of truth. `AGENTS.md` calls it `scoping.md`. When another doc or a skill disagrees with it, `scoping.md` wins, and you should flag the conflict.
- `docs/roadmap.md` has seven phases, each with "Decide before starting" items and a checklist for when it's done. Check those decisions before starting a phase. If one is still open, ask the user.
- `docs/tech-stack-decisions.md` is the decision log for tooling: what was chosen, what was rejected, and when to revisit. Add new decisions there in the same format.
- The `job-ingestion` skill listed in `AGENTS.md` doesn't exist yet under `.claude/skills/`. Apply the ingestion rules from `AGENTS.md` and `scoping.md` directly.

## Phase 1 scoring design (spans several docs)

- The user's labels are ground truth. There are two separate labels, a 1–5 fit score and would-apply (yes/no), each tagged with its rubric version. The user labels a job before any model score is shown.
- The model scores a job one requirement at a time. Each requirement gets a status (met, partial, not met, unclear) and a quoted resume line. Then it gives an overall 1–5 score with a rationale. Code checks every quote against the resume text, and fabricated quotes count as a metric.
- A deterministic coverage score is computed from the requirement statuses. A non-LLM keyword-overlap baseline goes in every comparison.
- Every experiment records the model ID and the prompt, rubric, and dataset versions. Pick a configuration by agreement with the user first, then consistency, then cost and latency.
