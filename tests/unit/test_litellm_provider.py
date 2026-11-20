"""The LiteLLM adapter, using LiteLLM's built-in mock responses: no network, no cost."""

import os
import subprocess
import sys
from typing import Any

import litellm
import pytest
from litellm import exceptions as llm_errors

from jobsearch.config import ModelConfig
from jobsearch.llm.providers.base import ProviderError
from jobsearch.llm.providers.litellm_provider import LiteLLMProvider
from jobsearch.llm.types import CallStatus, Message

CLOUD = ModelConfig(
    litellm_model="anthropic/claude-haiku-5-5",
    api_key_env="TEST_ANTHROPIC_KEY",
    context_window=100_000,
    max_output_tokens=1000,
    params={"reasoning_effort": "low"},
)
LOCAL = ModelConfig(
    litellm_model="ollama_chat/qwen3:14b",
    local=True,
    api_base="http://localhost:11434",
    context_window=16384,
    max_output_tokens=4000,
    params={"num_ctx": 16384},
)
MESSAGES = (Message("user", "Made-up job and resume."),)
SCHEMA = {"type": "object", "properties": {"score": {"type": "integer"}}}


class Recorder:
    """Stands in for litellm.completion: records kwargs, then runs a mock completion."""

    def __init__(self, mock: str = '{"score": 3}') -> None:
        self.kwargs: dict[str, Any] = {}
        self._mock = mock

    def __call__(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        return litellm.completion(**kwargs, mock_response=self._mock)


def test_cloud_request_is_built_from_config() -> None:
    recorder = Recorder()
    provider = LiteLLMProvider(completion_fn=recorder, environ={"TEST_ANTHROPIC_KEY": "sk-test"})
    completion = provider.complete(CLOUD, MESSAGES, SCHEMA)
    kwargs = recorder.kwargs
    assert kwargs["model"] == "anthropic/claude-haiku-5-5"
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["max_tokens"] == 1000
    assert kwargs["num_retries"] == 0  # LLMClient owns retries
    assert kwargs["reasoning_effort"] == "low"
    assert kwargs["response_format"]["json_schema"]["schema"] == SCHEMA
    assert kwargs["messages"] == [{"role": "user", "content": "Made-up job and resume."}]
    assert "temperature" not in kwargs
    assert completion.text == '{"score": 3}'
    assert completion.finish_reason == "stop"
    assert completion.input_tokens is not None and completion.output_tokens is not None


def test_local_request_uses_api_base_and_no_key() -> None:
    recorder = Recorder()
    LiteLLMProvider(completion_fn=recorder, environ={}).complete(LOCAL, MESSAGES, None)
    assert recorder.kwargs["api_base"] == "http://localhost:11434"
    assert recorder.kwargs["num_ctx"] == 16384
    assert "api_key" not in recorder.kwargs
    assert "response_format" not in recorder.kwargs


def test_missing_api_key_is_reported_before_calling() -> None:
    recorder = Recorder()
    with pytest.raises(ProviderError) as caught:
        LiteLLMProvider(completion_fn=recorder, environ={}).complete(CLOUD, MESSAGES, SCHEMA)
    assert caught.value.status is CallStatus.NOT_CONFIGURED
    assert "TEST_ANTHROPIC_KEY" in str(caught.value)
    assert recorder.kwargs == {}


def _raise(exc: Exception) -> Any:
    def completion(**kwargs: Any) -> Any:
        raise exc

    return completion


ARGS: dict[str, Any] = {"message": "provider said no", "model": "m", "llm_provider": "anthropic"}


@pytest.mark.parametrize(
    ("exc", "status", "retryable"),
    [
        (llm_errors.Timeout(**ARGS), CallStatus.TIMEOUT, True),
        (llm_errors.RateLimitError(**ARGS), CallStatus.RATE_LIMITED, True),
        (llm_errors.ContextWindowExceededError(**ARGS), CallStatus.CONTEXT_OVERFLOW, False),
        (llm_errors.ContentPolicyViolationError(**ARGS), CallStatus.REFUSED, False),
        (llm_errors.AuthenticationError(**ARGS), CallStatus.AUTH_ERROR, False),
        (llm_errors.NotFoundError(**ARGS), CallStatus.BAD_REQUEST, False),
        (llm_errors.BadRequestError(**ARGS), CallStatus.BAD_REQUEST, False),
        (llm_errors.ServiceUnavailableError(**ARGS), CallStatus.PROVIDER_ERROR, True),
        (llm_errors.InternalServerError(**ARGS), CallStatus.PROVIDER_ERROR, True),
        (llm_errors.APIConnectionError(**ARGS), CallStatus.PROVIDER_ERROR, True),
        (RuntimeError("boom"), CallStatus.PROVIDER_ERROR, False),
    ],
    ids=lambda value: type(value).__name__ if isinstance(value, Exception) else str(value),
)
def test_errors_map_to_statuses(exc: Exception, status: CallStatus, retryable: bool) -> None:
    provider = LiteLLMProvider(completion_fn=_raise(exc), environ={"TEST_ANTHROPIC_KEY": "k"})
    with pytest.raises(ProviderError) as caught:
        provider.complete(CLOUD, MESSAGES, None)
    assert (caught.value.status, caught.value.retryable) == (status, retryable)


def test_long_error_messages_are_shortened() -> None:
    exc = llm_errors.BadRequestError(message="x" * 2000, model="m", llm_provider="anthropic")
    provider = LiteLLMProvider(completion_fn=_raise(exc), environ={"TEST_ANTHROPIC_KEY": "k"})
    with pytest.raises(ProviderError) as caught:
        provider.complete(CLOUD, MESSAGES, None)
    assert len(str(caught.value)) < 400


def test_importing_the_provider_makes_no_network_lookups() -> None:
    # In a fresh interpreter, every DNS lookup fails and is recorded.
    script = (
        "import socket\n"
        "lookups = []\n"
        "def blocked(*args, **kwargs):\n"
        "    lookups.append(args[0])\n"
        "    raise OSError('blocked')\n"
        "socket.getaddrinfo = blocked\n"
        "import jobsearch.llm.providers.litellm_provider\n"
        "print(lookups)\n"
    )
    # Without the variable the provider sets, so the test can't pass by inheriting it.
    env = {k: v for k, v in os.environ.items() if k != "LITELLM_LOCAL_MODEL_COST_MAP"}
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        env=env,
    )
    assert out.stdout.strip() == "[]"
