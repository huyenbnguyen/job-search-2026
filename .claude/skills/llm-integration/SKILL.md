---
name: llm-integration
description: Rules for LLM provider adapters, prompts, output validation, retries, and cost tracking. Use before adding or changing a provider, a prompt, or any code that calls an LLM.
---

# LLM integration

## Provider interface
- Every model call goes through the provider-agnostic interface in `src/jobsearch/llm/providers/`: prompt and inputs in, validated structured output out.
- Scoring, evaluation, and tailoring code never import a provider SDK directly.
- Adding or swapping a model is a config change plus, at most, one new adapter.
- Model IDs come from config, never from code.
- Cloud and locally run models are both valid. A new adapter for a local runtime follows the same interface.

## Prompts
- Prompts live in versioned files under `prompts/`, grouped by task, for example:
  - `prompts/job_requirements/extract_v1.txt`
  - `prompts/job_matching/fit_score_v1.txt`
  - `prompts/job_matching/explanation_v1.txt`
  - `prompts/resume_tailoring/draft_v1.txt`
  - `prompts/resume_tailoring/claim_review_v1.txt`
- To change a prompt, add a new version. Never edit an old version in place.
- Record the prompt version with every prediction and experiment.
- Log each prompt change and its rationale in the decision log under `docs/`.
- Add few-shot examples only when an evaluation shows they fix a specific failure pattern.
- Job text is untrusted. Keep it clearly delimited as data in the prompt, and never let it change instructions, tools, or output schema.

## Output validation
- Define a Pydantic model for every structured output. Reject output that fails validation; don't repair it silently.
- Never ask the model to certify its own correctness. Correctness checks are deterministic code or the user.
- Record schema-validity failures; they are an evaluation metric.

## Reliability
- Every call has a timeout and bounded retries with exponential backoff and jitter, for transient errors only.
- Map provider timeouts and rate limits to explicit errors, and record a failure state instead of raising past the run.
- No autonomous loops or tool-calling.

## Cost
- Record input/output tokens, latency, and estimated cost for every call.
- Aggregate cost per job, per run, and per tailoring session.
- Scheduled runs enforce a model-spend cap from config and stop when it's reached.

## Privacy
- Send each call only what its task needs.
- Before sending resume text to a cloud provider, check its API data-retention and training-use settings.
- Never log prompts or responses that contain full resume text. Log IDs and redacted metadata.
