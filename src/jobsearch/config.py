"""Typed application config, loaded from YAML. See config/config.example.yaml."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


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


class AppConfig(_Section):
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from `path`, or from the default path if it exists, else use defaults."""
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return AppConfig()
        path = DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(raw)
