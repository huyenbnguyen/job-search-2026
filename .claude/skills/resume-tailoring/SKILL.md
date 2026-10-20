---
name: resume-tailoring
description: Rules for evidence-backed resume tailoring (Phase 4) — the claim ledger, clarifying questions, claim validation, user approval, and export. Use before any resume-tailoring work.
---

# Resume tailoring (Phase 4)

Don't start this until Phases 1–3 are done, unless the user asks.

## No invented content
- Never invent skills, responsibilities, metrics, dates, ownership, or accomplishments.
- Rewriting may rephrase, reorder, and select. It may not add facts.
- When a fact needed for a stronger draft is missing, ask the user instead of guessing. Store their answer as a user-confirmed fact.

## Claim ledger
- Every substantive claim in a draft links to a source fact: resume text or user-confirmed information.
- Each claim records its evidence, validation state, and review/approval status.
- A claim without evidence is marked unsupported and shown to the user. It is never silently kept.

## Validation
- Deterministic checks first: numbers, dates, technologies, and named employers in the draft must appear in the linked facts.
- An LLM claim check is a secondary detector, not proof. Give it only the draft and the facts, not the drafting reasoning.
- Unsupported claims are never marked as verified.

## Review and export
- Keep user edits distinct from model output.
- Export (DOCX initially) only after explicit user approval.
- Never overwrite the source resume. Each draft is versioned and tied to one job version.

## Inputs
- Validate uploaded resume file type and size.
- Resume parsing starts with text-based PDF and DOCX.

## Evaluation
- Track unsupported-claim rate, semantic preservation, requirement coverage, unresolved claims, user edits and rejections, cost per session, and time to approval.
- Human-audit a sample of drafts. Don't rely on an LLM judge alone.
