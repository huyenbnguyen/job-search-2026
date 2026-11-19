"""Keyword-overlap baseline. All text here is synthetic."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from jobsearch.config import KeywordBaselineConfig
from jobsearch.services.scoring.keyword_baseline import (
    Skill,
    SkillMatcher,
    Vocabulary,
    coverage_to_score,
    load_vocabulary,
    score_text,
)

VOCABULARY = Vocabulary(
    version="test_v1",
    skills=(
        Skill(name="Python"),
        Skill(name="Java"),
        Skill(name="JavaScript"),
        Skill(name="Go", terms=("golang",), exact_case=("Go",)),
        Skill(name="C++", terms=("c++",)),
        Skill(name="C#", terms=("c#",)),
        Skill(name="Kubernetes", terms=("kubernetes", "k8s")),
        Skill(name="Distributed systems", terms=("distributed system",)),
        Skill(name="API design", terms=("api",)),
    ),
)
THRESHOLDS = (20, 40, 60, 80)


@pytest.fixture
def matcher() -> SkillMatcher:
    return SkillMatcher(VOCABULARY)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We use Python and k8s.", ["Python", "Kubernetes"]),
        ("JavaScript only", ["JavaScript"]),
        ("Java, not JavaScript", ["Java", "JavaScript"]),
        ("Services in Go or Golang", ["Go"]),
        ("We go fast and let go of process", []),
        ("Modern C++ and C# code", ["C++", "C#"]),
        ("Design distributed-systems and REST APIs", ["Distributed systems", "API design"]),
        ("Pythonic tooling, kubernetesque", []),
    ],
)
def test_finds_whole_words_aliases_and_case_sensitive_names(
    matcher: SkillMatcher, text: str, expected: list[str]
) -> None:
    assert matcher.find(text) == expected


@pytest.mark.parametrize(
    ("matched", "total", "score"),
    [
        (0, 5, 1),
        (1, 6, 1),  # 16.7%
        (1, 5, 2),  # exactly 20%
        (2, 5, 3),  # exactly 40%
        (3, 5, 4),  # exactly 60%
        (7, 10, 4),
        (4, 5, 5),  # exactly 80%
        (5, 5, 5),
    ],
)
def test_coverage_maps_onto_whole_scores_with_exact_boundaries(
    matched: int, total: int, score: int
) -> None:
    result = coverage_to_score(matched, total, THRESHOLDS)
    assert result == score
    assert type(result) is int


@pytest.mark.parametrize(("matched", "total"), [(1, 0), (-1, 3), (4, 3)])
def test_coverage_needs_sensible_counts(matched: int, total: int) -> None:
    with pytest.raises(ValueError):
        coverage_to_score(matched, total, THRESHOLDS)


def test_score_is_the_share_of_job_skills_the_resume_has(matcher: SkillMatcher) -> None:
    description = "Python, Go, Kubernetes, and distributed systems experience."
    result = score_text(matcher, description, {"Python", "Kubernetes", "Java"}, THRESHOLDS)
    assert result is not None
    assert result.fit_score == 3
    assert result.matched == ["Python", "Kubernetes"]
    assert result.missing == ["Go", "Distributed systems"]


def test_description_without_known_skills_is_not_scored(matcher: SkillMatcher) -> None:
    assert (
        score_text(matcher, "Great benefits and a friendly team.", {"Python"}, THRESHOLDS) is None
    )


def test_vocabulary_rejects_duplicate_skill_names() -> None:
    with pytest.raises(ValidationError, match="duplicate skill names: Go"):
        Vocabulary(version="v", skills=(Skill(name="Go"), Skill(name="Go")))


def test_vocabulary_file_version_must_match_its_name(tmp_path: Path) -> None:
    (tmp_path / "skills_v2.yaml").write_text("version: skills_v1\nskills:\n  - name: Go\n")
    with pytest.raises(ValueError, match="expected 'skills_v2'"):
        load_vocabulary("skills_v2", tmp_path)


def test_checked_in_vocabulary_loads_and_compiles() -> None:
    vocabulary = load_vocabulary(KeywordBaselineConfig().vocabulary)
    matcher = SkillMatcher(vocabulary)
    assert matcher.find("Python services on Kubernetes (k8s) and PostgreSQL") == [
        "Python",
        "PostgreSQL",
        "Kubernetes",
    ]


@pytest.mark.parametrize(
    "percents",
    [(80, 60, 40, 20), (20, 20, 60, 80), (0, 40, 60, 80), (20, 40, 60, 101), (0.2, 0.4, 0.6, 0.8)],
)
def test_threshold_percents_must_be_ascending_whole_percentages(
    percents: tuple[float, ...],
) -> None:
    with pytest.raises(ValidationError):
        KeywordBaselineConfig.model_validate({"threshold_percents": percents})
