"""The interface every provider implements: send messages, get text and usage back."""

from dataclasses import dataclass
from typing import Any, Protocol

from jobsearch.config import ModelConfig
from jobsearch.llm.types import CallStatus, Message


@dataclass(frozen=True)
class Completion:
    text: str
    finish_reason: str | None
    # None when the provider didn't report usage.
    input_tokens: int | None
    output_tokens: int | None
    served_model: str | None


class ProviderError(Exception):
    """A call that produced no answer. The message must not quote the prompt."""

    def __init__(self, status: CallStatus, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class Provider(Protocol):
    def complete(
        self,
        model: ModelConfig,
        messages: tuple[Message, ...],
        json_schema: dict[str, Any] | None,
    ) -> Completion:
        """One attempt. Raise ProviderError for any failure; never retry here."""
        ...
