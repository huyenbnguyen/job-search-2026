"""The one provider adapter: LiteLLM, which speaks to Anthropic, Ollama and others.

This is the only module that imports litellm. Settings that keep it from contacting
anything except the model provider are applied before the import:
- LITELLM_LOCAL_MODEL_COST_MAP: otherwise importing litellm downloads its price list
  from GitHub. Prices come from our config instead.
LiteLLM's own retries are off (num_retries=0); LLMClient retries, so every provider
behaves the same and the behavior is tested.
"""

import os
from collections.abc import Callable, Mapping
from typing import Any

os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"

import litellm
from litellm import exceptions as llm_errors

from jobsearch.config import ModelConfig
from jobsearch.llm.providers.base import Completion, ProviderError
from jobsearch.llm.types import CallStatus, Message

litellm.drop_params = False  # An unsupported setting is an error, not silently dropped.
litellm.suppress_debug_info = True

# Most specific first: several of these subclass the later ones.
_ERROR_MAP: tuple[tuple[type[Exception], CallStatus, bool], ...] = (
    (llm_errors.Timeout, CallStatus.TIMEOUT, True),
    (llm_errors.RateLimitError, CallStatus.RATE_LIMITED, True),
    (llm_errors.ContextWindowExceededError, CallStatus.CONTEXT_OVERFLOW, False),
    (llm_errors.ContentPolicyViolationError, CallStatus.REFUSED, False),
    (llm_errors.AuthenticationError, CallStatus.AUTH_ERROR, False),
    (llm_errors.PermissionDeniedError, CallStatus.AUTH_ERROR, False),
    (llm_errors.NotFoundError, CallStatus.BAD_REQUEST, False),
    (llm_errors.UnsupportedParamsError, CallStatus.BAD_REQUEST, False),
    (llm_errors.BadRequestError, CallStatus.BAD_REQUEST, False),
    (llm_errors.UnprocessableEntityError, CallStatus.BAD_REQUEST, False),
    (llm_errors.ServiceUnavailableError, CallStatus.PROVIDER_ERROR, True),
    (llm_errors.InternalServerError, CallStatus.PROVIDER_ERROR, True),
    (llm_errors.APIConnectionError, CallStatus.PROVIDER_ERROR, True),
)
_MAX_ERROR_CHARS = 300

CompletionFn = Callable[..., Any]


class LiteLLMProvider:
    def __init__(
        self,
        *,
        completion_fn: CompletionFn | None = None,
        environ: Mapping[str, str] = os.environ,
    ) -> None:
        self._completion = completion_fn or litellm.completion
        self._environ = environ

    def complete(
        self,
        model: ModelConfig,
        messages: tuple[Message, ...],
        json_schema: dict[str, Any] | None,
    ) -> Completion:
        kwargs: dict[str, Any] = {
            **model.params,
            "model": model.litellm_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": model.max_output_tokens,
            "timeout": model.timeout_seconds,
            "num_retries": 0,
        }
        if model.api_base:
            kwargs["api_base"] = model.api_base
        if model.api_key_env:
            key = self._environ.get(model.api_key_env)
            if not key:
                raise ProviderError(
                    CallStatus.NOT_CONFIGURED, f"environment variable {model.api_key_env} isn't set"
                )
            kwargs["api_key"] = key
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": json_schema, "strict": True},
            }

        try:
            response = self._completion(**kwargs)
        except Exception as exc:
            raise _map_error(exc) from exc
        return _to_completion(response)


def _map_error(exc: Exception) -> ProviderError:
    for error_type, status, retryable in _ERROR_MAP:
        if isinstance(exc, error_type):
            return ProviderError(status, _short(exc), retryable=retryable)
    if isinstance(exc, llm_errors.APIError):
        code = getattr(exc, "status_code", None)
        retryable = isinstance(code, int) and code >= 500
        return ProviderError(CallStatus.PROVIDER_ERROR, _short(exc), retryable=retryable)
    return ProviderError(CallStatus.PROVIDER_ERROR, f"unexpected {type(exc).__name__}")


def _short(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    if len(text) > _MAX_ERROR_CHARS:
        text = text[:_MAX_ERROR_CHARS] + "..."
    return f"{type(exc).__name__}: {text}"


def _to_completion(response: Any) -> Completion:
    try:
        choice = response.choices[0]
    except (AttributeError, IndexError) as exc:
        raise ProviderError(CallStatus.PROVIDER_ERROR, "response has no answer") from exc
    usage = getattr(response, "usage", None)
    return Completion(
        text=choice.message.content or "",
        finish_reason=choice.finish_reason,
        input_tokens=_count(getattr(usage, "prompt_tokens", None)),
        output_tokens=_count(getattr(usage, "completion_tokens", None)),
        served_model=getattr(response, "model", None),
    )


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None
