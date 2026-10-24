# Ship a change

Follow these steps in order. Don't skip one, and don't move on while a step is failing.

## 1. Sync main

```bash
git status                 # working tree must be clean
git checkout main
git pull origin main
```

- If there are uncommitted changes, stop and ask the user what to do. Never stash, reset, or discard their work on your own.
- If the pull has conflicts or fails, stop and report it.

## 2. Create a branch off main

```bash
git checkout -b <type>/<short-description>
```

- `<type>` is one of `feat`, `fix`, `docs`, `refactor`, `test`, or `chore`; for example `feat/url-ingestion`.
- Never commit directly to `main`.

## 3. Make the change and run the tests

- Make the smallest change that does the job, following `AGENTS.md` and any skill for the area you're touching.
- Run the full checks using the commands in the `AGENTS.md` Commands section:
  - tests (pytest)
  - lint and type checks
  - evaluation regression checks, when prompts, scoring, or models changed
- Every check must pass. If a test fails, fix the cause; never skip, delete, or weaken a test to get green.
- If the commands are still TBD, tell the user which checks you couldn't run.

## 4. Request approval

Before committing, show the user:

- a short summary of what changed and why
- the files changed (`git diff --stat`)
- the test, lint, and type-check results
- anything risky or left undone

Then wait for an explicit yes. A lack of objection is not approval. If no one is available to approve, stop here and leave the branch uncommitted.

After approval, commit with a clear message describing the change.

## 5. Run code review

- Run the `code-review` command or skill on the branch's diff against `main` (`git diff main...HEAD`).
- If it isn't available, have a separate reviewer that hasn't seen your reasoning review the diff.
- Check in particular:
  - the rules in `AGENTS.md`, such as no secrets or personal data, nothing inline that should be config, versioned prompts, and LLM output validated
  - tests covering the change
- Fix real findings in new commits, then rerun step 3's checks. If a fix changes behavior significantly, ask for approval again.
- List any findings you chose not to fix, with the reason, for the pull request.

## 6. Open a pull request

```bash
git push -u origin <branch>
gh pr create --base main --title "<type>: <short summary>" --body-file <pr-body.md>
```

Use this body:

```markdown
## Summary
What changed and why, in 2–3 sentences.

## Changes
- Main changes, one per line

## Testing
- Checks run and their results
- Evaluation results, if prompts, scoring, or models changed

## Code review
- Findings fixed
- Findings not fixed, and why

## Notes
Follow-ups, open questions, or decision-log entries added.
```

- Share the pull request link with the user.
- Never merge the pull request yourself, and never force-push to `main`.