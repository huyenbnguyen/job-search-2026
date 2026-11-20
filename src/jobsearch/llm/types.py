"""Requests and results for model calls, independent of any provider SDK."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class CallStatus(StrEnum):
    """How a call ended. Every outcome is a status, so a bad model never crashes a run."""

    OK = "ok"
    # The answer wasn't valid JSON, or didn't match the requested schema.
    INVALID_OUTPUT = "invalid_output"
    # The answer was cut off at max_output_tokens.
    TRUNCATED = "truncated"
    # The model or the provider's safety system declined to answer.
    REFUSED = "refused"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    AUTH_ERROR = "auth_error"
    # The prompt plus the answer wouldn't fit the model's context window. Not sent.
    CONTEXT_OVERFLOW = "context_overflow"
    # The provider rejected the request itself, e.g. an unknown model or setting.
    BAD_REQUEST = "bad_request"
    # The provider or local runtime failed or couldn't be reached.
    PROVIDER_ERROR = "provider_error"
    # Not sent: it could take spend past llm.max_usd_total, or no cap is set.
    BUDGET_EXHAUSTED = "budget_exhausted"
    # Not sent: the API key's environment variable isn't set.
    NOT_CONFIGURED = "not_configured"


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user"]
    content: str


@dataclass(frozen=True)
class LLMRequest[T: BaseModel]:
    messages: tuple[Message, ...]
    # The answer must be a JSON object matching this model.
    output_schema: type[T]


@dataclass(frozen=True)
class LLMResult[T: BaseModel]:
    model_alias: str
    status: CallStatus
    # The validated answer; set only when status is OK.
    parsed: T | None = None
    # The model's raw answer text. May quote the prompt, so it's never logged.
    text: str | None = None
    # The model ID the provider reports having used.
    served_model: str | None = None
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # True when the provider didn't report usage and the counts are our estimate.
    tokens_estimated: bool = False
    # Time for the last attempt, from sending the request to having the answer.
    latency_ms: int = 0
    # US dollars x 1,000,000. Price per million tokens is micro-dollars per token.
    cost_micros: int = 0
    attempts: int = 0
    # A short reason for a non-OK status, for the person running the command.
    error: str | None = None

    @property
    def cost_usd(self) -> float:
        return self.cost_micros / 1_000_000
