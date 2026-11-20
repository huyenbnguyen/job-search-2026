"""Non-LLM baseline: score a job by how many of its listed skills the resume mentions.

1. Find the vocabulary skills a job description mentions (the job's skills).
2. Count how many of those skills the resume also mentions.
3. Map that onto the rubric's integer 1-5 scale: each configured percentage reached adds
   a point to a base of 1. Integer arithmetic only, so no rounding at a boundary.

It ignores seniority, years of experience, whether a skill is required or preferred, and
whether a company's name is also a listed skill. An LLM has to beat it to be
worth its cost. A job whose description mentions no vocabulary skill isn't scored.

Scores go to `fit_predictions` as provider `baseline`, with the vocabulary version as the
prompt version. Rerunning with unchanged inputs adds nothing.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobsearch.config import KeywordBaselineConfig
from jobsearch.models.tables import FitPrediction, Job, ResumeVersion
from jobsearch.paths import PROMPTS_DIR

PROVIDER = "baseline"
MODEL_ID = "keyword_overlap"
JOB_MATCHING_DIR = PROMPTS_DIR / "job_matching"


class Skill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    terms: tuple[str, ...] = ()
    exact_case: tuple[str, ...] = ()


class Vocabulary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    skills: tuple[Skill, ...]

    @model_validator(mode="after")
    def _unique_names(self) -> "Vocabulary":
        names = [skill.name for skill in self.skills]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate skill names: {', '.join(duplicates)}")
        return self


def load_vocabulary(version: str, directory: Path = JOB_MATCHING_DIR) -> Vocabulary:
    path = directory / f"{version}.yaml"
    vocabulary = Vocabulary.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if vocabulary.version != version:
        raise ValueError(f"{path.name} says version {vocabulary.version!r}, expected {version!r}")
    return vocabulary


def _pattern(term: str) -> str:
    # Whole words only. Spaces also match hyphens, and a trailing "s" is allowed.
    body = r"[\s-]+".join(re.escape(word) for word in term.split())
    return rf"(?<![\w+#]){body}s?(?![\w+#])"


class SkillMatcher:
    def __init__(self, vocabulary: Vocabulary) -> None:
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for skill in vocabulary.skills:
            any_case = skill.terms or (() if skill.exact_case else (skill.name,))
            alternatives = [f"(?i:{_pattern(term)})" for term in any_case]
            alternatives += [_pattern(term) for term in skill.exact_case]
            self._patterns.append((skill.name, re.compile("|".join(alternatives))))

    def find(self, text: str) -> list[str]:
        """Names of the skills `text` mentions, in vocabulary order."""
        return [name for name, pattern in self._patterns if pattern.search(text)]


@dataclass(frozen=True)
class KeywordScore:
    fit_score: int
    matched: list[str]
    missing: list[str]


def coverage_to_score(matched: int, total: int, threshold_percents: Iterable[int]) -> int:
    """1 plus the number of thresholds reached, where matched/total >= percent/100."""
    if not 0 <= matched <= total or total < 1:
        raise ValueError(f"need 0 <= matched <= total and total >= 1, got {matched}/{total}")
    return 1 + sum(100 * matched >= percent * total for percent in threshold_percents)


def score_text(
    matcher: SkillMatcher,
    description: str,
    resume_skills: set[str],
    threshold_percents: Iterable[int],
) -> KeywordScore | None:
    job_skills = matcher.find(description)
    if not job_skills:
        return None
    matched = [skill for skill in job_skills if skill in resume_skills]
    return KeywordScore(
        fit_score=coverage_to_score(len(matched), len(job_skills), threshold_percents),
        matched=matched,
        missing=[skill for skill in job_skills if skill not in resume_skills],
    )


@dataclass
class BaselineRunSummary:
    jobs_scored: int = 0
    # Already scored with the same resume, vocabulary, rubric and threshold percents.
    jobs_unchanged: int = 0
    # The description mentions no vocabulary skill.
    jobs_without_skills: list[int] = field(default_factory=list)
    # Predictions this run stored.
    prediction_ids: list[int] = field(default_factory=list)
    # One prediction per scored job for these inputs: this run's, or the stored one it reused.
    current_prediction_ids: list[int] = field(default_factory=list)


def run_keyword_baseline(
    session: Session,
    resume: ResumeVersion,
    config: KeywordBaselineConfig,
    rubric_version: str,
    run_id: str,
) -> BaselineRunSummary:
    """Score every job that has a description. Safe to rerun."""
    if not (JOB_MATCHING_DIR / f"{rubric_version}.md").is_file():
        raise ValueError(f"no rubric file for {rubric_version!r} in {JOB_MATCHING_DIR}")
    vocabulary = load_vocabulary(config.vocabulary)
    matcher = SkillMatcher(vocabulary)
    resume_skills = set(matcher.find(resume.text))
    thresholds = list(config.threshold_percents)
    summary = BaselineRunSummary()
    jobs = session.scalars(
        select(Job).where(Job.description_text.is_not(None)).order_by(Job.id)
    ).all()
    for job in jobs:
        assert job.description_text is not None
        earlier = _earlier_prediction(
            session, job.id, vocabulary.version, rubric_version, resume, thresholds
        )
        if earlier is not None:
            summary.jobs_unchanged += 1
            summary.current_prediction_ids.append(earlier)
            continue
        result = score_text(matcher, job.description_text, resume_skills, thresholds)
        if result is None:
            summary.jobs_without_skills.append(job.id)
            continue
        details: dict[str, Any] = {
            "matched_count": len(result.matched),
            "job_skill_count": len(result.matched) + len(result.missing),
            "matched": result.matched,
            "missing": result.missing,
            "threshold_percents": thresholds,
        }
        prediction = FitPrediction(
            job_id=job.id,
            fit_score=result.fit_score,
            provider=PROVIDER,
            model_id=MODEL_ID,
            prompt_version=vocabulary.version,
            rubric_version=rubric_version,
            resume_version=resume.text_hash,
            run_id=run_id,
            created_at=datetime.now(UTC),
            details=details,
        )
        session.add(prediction)
        session.flush()
        summary.prediction_ids.append(prediction.id)
        summary.current_prediction_ids.append(prediction.id)
        summary.jobs_scored += 1
    return summary


def _earlier_prediction(
    session: Session,
    job_id: int,
    vocabulary_version: str,
    rubric_version: str,
    resume: ResumeVersion,
    thresholds: list[int],
) -> int | None:
    """The newest stored prediction for this job with the same inputs, or None."""
    earlier = session.execute(
        select(FitPrediction.id, FitPrediction.details)
        .where(
            FitPrediction.job_id == job_id,
            FitPrediction.provider == PROVIDER,
            FitPrediction.model_id == MODEL_ID,
            FitPrediction.prompt_version == vocabulary_version,
            FitPrediction.rubric_version == rubric_version,
            FitPrediction.resume_version == resume.text_hash,
        )
        .order_by(FitPrediction.id.desc())
    ).all()
    return next(
        (
            pid
            for pid, details in earlier
            if details and details.get("threshold_percents") == thresholds
        ),
        None,
    )
