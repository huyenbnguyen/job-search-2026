"""Typed application config, loaded from YAML. See config/config.example.yaml."""

from collections.abc import Hashable
from pathlib import Path
from typing import Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    StrictInt,
    field_validator,
    model_validator,
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


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# Settings that choose where a request goes or which credentials it uses. They have their
# own fields (api_base, api_key_env), which are validated; params can't override them.
_RESERVED_PARAMS = frozenset(
    {
        "api_base", "api_key", "api_version", "base_url", "custom_llm_provider",
        "extra_headers", "fallbacks", "headers", "max_tokens", "messages", "model",
        "num_retries", "response_format", "timeout",
    }
)  # fmt: skip


class ModelPrice(_Section):
    """US dollars per million tokens. Copied from the provider's price list; 0 for local."""

    input: NonNegativeFloat
    output: NonNegativeFloat


class ModelConfig(_Section):
    """One model the app can call. Adding a model to try is adding one of these."""

    # The model in LiteLLM's "provider/model" form, e.g. "anthropic/claude-sonnet-5-5"
    # or "ollama_chat/qwen3:14b".
    litellm_model: str = Field(min_length=3)
    # Runs on this machine (Ollama). Then api_base must point at localhost, so the
    # resume can't leave the machine through a model marked local.
    local: bool = False
    api_base: str | None = None
    # The name of the environment variable that holds the API key, never the key itself.
    api_key_env: str | None = None
    # The largest prompt plus answer the model accepts, in tokens. Checked before every
    # call, because some runtimes cut off a long prompt silently instead of failing.
    context_window: PositiveInt
    max_output_tokens: PositiveInt = 8000
    # Required for cloud models, so a missing price can't make a paid model look free.
    price_per_mtok: ModelPrice = Field(default_factory=lambda: ModelPrice(input=0, output=0))
    # json_schema: the model is asked for JSON matching the schema natively.
    # prompt_only: the schema is written into the prompt instead, for models without
    # native support. Either way, the answer is validated before it's used.
    structured_output: Literal["json_schema", "prompt_only"] = "json_schema"
    # Extra settings passed to LiteLLM as they are, e.g. {num_ctx: 16384} for Ollama.
    # Claude models reject temperature, so don't set it for them.
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: PositiveFloat = 120.0

    @field_validator("params")
    @classmethod
    def _no_routing_params(cls, value: dict[str, Any]) -> dict[str, Any]:
        if reserved := sorted(_RESERVED_PARAMS & set(value)):
            raise ValueError(
                f"params can't set {', '.join(reserved)}: use the model's own fields instead"
            )
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.max_output_tokens >= self.context_window:
            raise ValueError("max_output_tokens must be smaller than context_window")
        if self.local:
            host = urlsplit(self.api_base or "").hostname
            if host not in _LOCAL_HOSTS:
                raise ValueError("a local model needs an api_base on localhost")
            if self.api_key_env:
                raise ValueError("a local model doesn't use api_key_env")
        else:
            if not self.api_key_env:
                raise ValueError("a cloud model needs api_key_env (the API key's variable name)")
            if "price_per_mtok" not in self.model_fields_set:
                raise ValueError("a cloud model needs price_per_mtok, from its price list")
        num_ctx = self.params.get("num_ctx")
        if isinstance(num_ctx, int) and num_ctx < self.context_window:
            raise ValueError("context_window can't be larger than params.num_ctx")
        return self


class LLMConfig(_Section):
    # Spend allowed on cloud models, in US dollars. No default: until it's set, cloud
    # models are not called at all. Counted per command for now; it becomes a total
    # across runs once scoring stores its calls in the database.
    max_usd_total: NonNegativeFloat | None = None
    # Retries apply only to temporary failures: timeouts, rate limits, server errors.
    max_retries: int = Field(default=2, ge=0)
    backoff_base_seconds: float = Field(default=2.0, ge=0)
    # Models by the short name used on the command line, e.g. "sonnet".
    models: dict[str, ModelConfig] = Field(default_factory=dict)


class AppConfig(_Section):
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)


def load_config(path: Path | None = None) -> AppConfig:
    """Load config from `path`, or from the default path if it exists, else use defaults."""
    if path is None:
        if not DEFAULT_CONFIG_PATH.exists():
            return AppConfig()
        path = DEFAULT_CONFIG_PATH
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader) or {}
    return AppConfig.model_validate(raw)
