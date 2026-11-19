# Baseline job matching: keyword overlap

The keyword baseline is a simple, non-LLM fit score. Every LLM scorer is compared against it, to show whether the model is worth its cost. It's run with `jobsearch score baseline` (see the README), and the code is in `src/jobsearch/services/scoring/keyword_baseline.py`.

## How a job is scored

1. It finds which skills from a fixed list (`prompts/job_matching/keyword_skills_v1.yaml`) the job description mentions. Call that count `total`.
2. It counts how many of those skills the resume also mentions. Call that `matched`.
3. It turns `matched` out of `total` into a whole-number score from 1 to 5, using the percentages in `scoring.keyword_baseline.threshold_percents` in config (default `[20, 40, 60, 80]`).

A job whose description mentions none of the listed skills (`total` = 0) gets no score.

## How skills are matched

The skill list is generic: it was written without reference to any one resume or to the user's labels. Each skill has a name and the terms that count as a mention of it, for example `Kubernetes` with `kubernetes` and `k8s`.

- Terms match as whole words or phrases, in any case. "Java" doesn't match inside "JavaScript".
- Names that are also everyday words match only with their exact capitalization, listed under `exact_case`. "Go" the language matches, but "let go of" doesn't.
- A space in a term also matches a hyphen ("distributed systems" matches "distributed-systems"), and a trailing "s" is allowed ("API" matches "APIs").

The list is versioned. Never edit a version that stored scores already use. Copy it to `keyword_skills_v2.yaml` and set `scoring.keyword_baseline.vocabulary` instead.

## From a share of skills to a 1–5 score

Every score starts at 1. Each threshold the share reaches adds one point:

| Share of the job's skills in the resume | Thresholds reached | Score |
|---|---|---|
| under 20% | none | 1 |
| 20% to under 40% | 20 | 2 |
| 40% to under 60% | 20, 40 | 3 |
| 60% to under 80% | 20, 40, 60 | 4 |
| 80% or more | all four | 5 |

The share is never computed as a decimal. A threshold of `percent` is reached when

```
100 × matched  ≥  percent × total
```

which is the same test as `matched / total ≥ percent / 100`, with both sides multiplied by `100 × total` so only whole numbers are involved. Every value in the calculation is an integer, so a share that lands exactly on a threshold (3 of 5 is exactly 60%) always counts as reaching it. No rounding is involved, and the stored score is always the integer 1, 2, 3, 4 or 5.

### A worked example

A job mentions Python, Go, Kubernetes, PostgreSQL and Kafka (`total` = 5). The resume mentions Python, Kubernetes and PostgreSQL (`matched` = 3).

| Threshold | Test | Reached? |
|---|---|---|
| 20 | 100 × 3 = 300 ≥ 20 × 5 = 100 | yes |
| 40 | 300 ≥ 40 × 5 = 200 | yes |
| 60 | 300 ≥ 60 × 5 = 300 | yes, exactly on the boundary |
| 80 | 300 ≥ 80 × 5 = 400 | no |

Three thresholds reached, so the score is 1 + 3 = **4**.

### More examples

| Matched / total | Share | Thresholds reached | Score |
|---|---|---|---|
| 0 / 4 | 0% | none | 1 |
| 1 / 6 | about 17% | none | 1 |
| 1 / 5 | exactly 20% | 20 | 2 |
| 2 / 5 | exactly 40% | 20, 40 | 3 |
| 8 / 15 | about 53% | 20, 40 | 3 |
| 12 / 18 | about 67% | 20, 40, 60 | 4 |
| 4 / 5 | exactly 80% | all four | 5 |
| 9 / 11 | about 82% | all four | 5 |

The "Share" column is for reading only; the code never computes it.

### Why these thresholds

The thresholds are equal steps across the range. They weren't fitted to the user's labels, which would make the comparison with those labels meaningless. To try other cut-offs, change them in config. Each set of thresholds is stored as a separate set of baseline scores, so earlier results stay comparable.

## Known limits

These are deliberate. A baseline should be simple, and an LLM scorer should do better on exactly these points.

- No stemming: "mentored" doesn't match "mentoring".
- Required and preferred skills count the same.
- Seniority, years of experience, and the job's domain are ignored, so a clear mismatch rarely scores below 3.
- A company whose name is also a listed skill counts as a match in its own job posting: a database vendor's posting names its own database.

## What's stored

Each score is a row in `fit_predictions` with:
- provider `baseline` and model `keyword_overlap`
- the skill list version as the prompt version
- the rubric version and the resume version (the resume text's hash)
- `details` (JSON): the matched and missing skills, their counts, and the threshold percents

Rerunning with the same resume, skill list, rubric and thresholds adds nothing. `jobsearch score baseline` shows scores only for jobs already labeled, so the baseline can't anchor the user's own scores.

To see what each job was missing:

```bash
sqlite3 ~/.local/share/jobsearch/jobsearch.db \
  "SELECT job_id, fit_score, json_extract(details, '$.missing') FROM fit_predictions WHERE provider = 'baseline';"
```
