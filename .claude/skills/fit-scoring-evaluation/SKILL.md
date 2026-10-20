---
name: fit-scoring-evaluation
description: Rules for the fit-scoring rubric, human labels, model and prompt experiments, metrics, model selection, and the feedback loop. Use before working on labeling, scoring, evaluation, or feedback code.
---

# Fit scoring and evaluation

The draft rubric and the full rationale are in `scoping.md`, Section 2 ("Fit-Scoring Rubric").

## Rubric and labels
- Users and models score on the same 1–5 anchored rubric, stored as a versioned file (proposed: `prompts/job_matching/rubric_v1.md`).
- Record the rubric version with every label and prediction. Never compare scores made under different rubric versions.
- The user gives two labels per job:
  - **Fit score (1–5):** qualification fit only. Models are graded against this.
  - **Would apply (yes/no):** overall interest. Never used to grade the fit scorer.
- Store human labels separately from model scores.
- Applied-job URLs are seed examples, not ground truth. A job counts as labeled only after the user labels it.

## Labeling flow
- The user scores a job before any model output exists for it. Never show model scores in the labeling flow.
- Support a re-scoring pass without showing earlier scores, to measure the user's own consistency.
- Help the user cover the full 1–5 range; applied-to jobs skew toward 4–5.

## Fit-scoring output
- For each job requirement: a status (met, partial, not met, unclear) and a quoted resume line as evidence. Then an overall 1–5 score and a short rationale.
- Code checks every quote against the resume text. A fabricated quote fails validation and counts toward the fabricated-quote rate.
- Also compute a deterministic score from requirement coverage (weights in config), and report it alongside the model's score.

## Experiments
- The experiment runner covers models × prompt versions × jobs, and always includes a non-LLM baseline (keyword/skill overlap mapped onto 1–5).
- Every experiment records: provider/model ID, prompt version, rubric version, dataset version, input/output tokens, latency, estimated cost, and schema validity.
- Repeat a subset of jobs to measure run-to-run variance.

## Metrics
- Fit scoring (Phase 1 onward): mean absolute error, within-one agreement, mean signed error, Spearman rank correlation, confusion matrix, run-to-run variance, schema-validity rate, fabricated-quote rate, cost, and latency.
- Report bootstrap confidence intervals on the main metrics. Treat models with heavily overlapping intervals as tied.
- Ranking (Phase 2 onward): NDCG@10 (primary), Precision@10, Recall@10, plus constraint-violation rate and extraction accuracy.

## Choosing a model
- Order: agreement with the user's fit scores, then consistency, then cost and latency. Among ties, prefer the cheaper model.
- Never switch the production configuration automatically. Promote it explicitly after review and record the decision.

## Data discipline
- Once enough labels exist (Phase 3), split into development and held-out sets. Iterate on development; confirm on held-out.
- Never tune prompts, weights, or model choice on held-out data.
- With small datasets, use cross-validation or leave-one-out and report uncertainty. Never claim an improvement from a tiny set.
- Never invent numeric targets or results.

## Feedback loop (Phase 3)
- Store feedback (fit labels, rejection reasons, corrections) append-only, linked to the exact job version and prediction.
- Feedback updates the evaluation set first. It never silently changes ranking behavior; changes are measured offline before promotion.
- Turn representative failures into regression tests or reviewed evaluation cases.
