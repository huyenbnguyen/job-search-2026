"""Typed application config, loaded from YAML. See config/config.example.yaml."""

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    StrictInt,
    field_validator,
)

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects a key repeated in the same mapping, at any depth.

    Plain safe_load keeps the last value silently, so a repeated section would hide
    the settings written above it.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, Hashable):
                continue  # SafeLoader reports unhashable keys itself.
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FetchConfig(_Section):
    timeout_seconds: PositiveFloat = 20.0
    max_response_bytes: PositiveInt = 5_000_000
    max_redirects: int = Field(default=5, ge=0)
    max_retries: int = Field(default=3, ge=0)
    backoff_base_seconds: float = Field(default=1.0, ge=0)
    per_host_delay_seconds: float = Field(default=2.0, ge=0)
    user_agent: str = "jobsearch-personal/0.1 (single-user job tracker)"
    respect_robots_txt: bool = True


class IngestionConfig(_Section):
    fetch: FetchConfig = Field(default_factory=FetchConfig)


class LoggingConfig(_Section):
    file: Path = Path("data/logs/jobsearch.log")


class DatabaseConfig(_Section):
    # Outside the repository, so the database file can never be committed.
    path: Path = Field(default=Path("~/.local/share/jobsearch/jobsearch.db"), validate_default=True)
    busy_timeout_ms: PositiveInt = 5000

    @field_validator("path")
    @classmethod
    def _expand_home(cls, value: Path) -> Path:
        return value.expanduser()


class KeywordBaselineConfig(_Section):
    # A file in prompts/job_matching/, without the .yaml extension.
    vocabulary: str = "keyword_skills_v2"
    # Whole percentages of the job's skills found in the resume. Reaching each one adds a
    # point to a base score of 1, so four ascending thresholds give the integer scores 1-5.
    # Compared in integer arithmetic: no floating point anywhere in the score.
    threshold_percents: tuple[StrictInt, StrictInt, StrictInt, StrictInt] = (20, 40, 60, 80)

    @field_validator("threshold_percents")
    @classmethod
    def _ascending_percents(cls, value: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        if list(value) != sorted(set(value)) or not all(1 <= t <= 100 for t in value):
            raise ValueError("threshold_percents must be four distinct ascending integers, 1-100")
        return value


class ScoringConfig(_Section):
    # The rubric that scores are compared on: a file in prompts/job_matching/.
    rubric_version: str = "rubric_v1"
    keyword_baseline: KeywordBaselineConfig = Field(default_factory=KeywordBaselineConfig)


class AppConfig(_Section):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from `path`, or from the default path if it exists, else use defaults."""
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return AppConfig()
        path = DEFAULT_CONFIG_PATH
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    return AppConfig.model_validate(raw)
