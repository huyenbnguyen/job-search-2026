"""LLMClient with a scripted provider: no network, no cost. All data is synthetic."""

from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jobsearch.config import LLMConfig, ModelConfig, ModelPrice
from jobsearch.llm.client import LLMClient, UnknownModelError, cost_micros, estimate_tokens
from jobsearch.llm.parsing import OutputParseError, parse_output
from jobsearch.llm.providers.base import Completion, ProviderError
from jobsearch.llm.types import CallStatus, LLMRequest, Message


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)


REQUEST = LLMRequest(
    messages=(Message("user", "Score this made-up job."),),
    output_schema=Answer,
    prompt_version="test/answer_v1",
)

FREE = ModelPrice(input=0, output=0)
LOCAL = ModelConfig(
    litellm_model="ollama_chat/test-model",
    local=True,
    api_base="http://localhost:11434",
    context_window=4000,
    max_output_tokens=500,
)
PAID = ModelConfig(
    litellm_model="anthropic/test-model",
    api_key_env="TEST_KEY",
    context_window=100_000,
    max_output_tokens=1000,
    price_per_mtok={"input": 2.0, "output": 10.0},
)


def ok(text: str = '{"score": 4}', **fields: Any) -> Completion:
    defaults: dict[str, Any] = {
        "finish_reason": "stop",
        "input_tokens": 100,
        "output_tokens": 20,
        "served_model": "test-model-001",
    }
    return Completion(text=text, **{**defaults, **fields})


class ScriptedProvider:
    """Returns or raises the scripted outcomes in order; records every call."""

    def __init__(self, *outcomes: Completion | ProviderError) -> None:
        self._outcomes: Iterator[Completion | ProviderError] = iter(outcomes)
        self.calls: list[tuple[ModelConfig, tuple[Message, ...], dict[str, Any] | None]] = []

    def complete(
        self, model: ModelConfig, messages: tuple[Message, ...], json_schema: dict[str, Any] | None
    ) -> Completion:
        self.calls.append((model, messages, json_schema))
        outcome = next(self._outcomes)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome


def make_client(
    provider: ScriptedProvider, *, cap: float | None = 1.0, spent: int = 0, **models: ModelConfig
) -> tuple[LLMClient, list[float]]:
    sleeps: list[float] = []
    config = LLMConfig(
        max_usd_total=cap, max_retries=2, backoff_base_seconds=0.0, models=models or {"m": LOCAL}
    )
    return LLMClient(config, provider, spent_before_micros=spent, sleep=sleeps.append), sleeps


def test_valid_answer_is_parsed_and_measured() -> None:
    provider = ScriptedProvider(ok())
    client, _ = make_client(provider, paid=PAID)
    result = client.complete("paid", REQUEST)
    assert result.status is CallStatus.OK
    assert result.parsed == Answer(score=4)
    assert (result.input_tokens, result.output_tokens) == (100, 20)
    # 100 x $2/M + 20 x $10/M = 200 + 200 micro-dollars
    assert result.cost_micros == 400
    assert result.cost_usd == pytest.approx(0.0004)
    assert result.served_model == "test-model-001"
    assert result.attempts == 1
    assert client.spent_micros == 400


def test_native_schema_is_sent_separately() -> None:
    provider = ScriptedProvider(ok())
    client, _ = make_client(provider)
    client.complete("m", REQUEST)
    _, messages, json_schema = provider.calls[0]
    assert json_schema == Answer.model_json_schema()
    assert messages == REQUEST.messages


def test_prompt_only_models_get_the_schema_in_the_prompt() -> None:
    provider = ScriptedProvider(ok())
    model = LOCAL.model_copy(update={"structured_output": "prompt_only"})
    client, _ = make_client(provider, m=model)
    assert client.complete("m", REQUEST).status is CallStatus.OK
    _, messages, json_schema = provider.calls[0]
    assert json_schema is None
    assert len(messages) == 2
    assert '"score"' in messages[-1].content and "JSON Schema" in messages[-1].content


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("Sure! The score is 4.", "not valid JSON"),
        ('["score", 4]', "expected a JSON object"),
        ('{"score": 9}', "schema error"),
        ('{"score": 4, "extra": true}', "schema error"),
    ],
)
def test_invalid_answers_are_recorded_not_repaired(text: str, error: str) -> None:
    client, _ = make_client(ScriptedProvider(ok(text)))
    result = client.complete("m", REQUEST)
    assert result.status is CallStatus.INVALID_OUTPUT
    assert result.parsed is None
    assert error in (result.error or "")
    assert result.text == text


def test_thinking_and_code_fences_are_stripped() -> None:
    text = '<think>The job wants Python.\n{"score": 1}</think>\n```json\n{"score": 3}\n```'
    client, _ = make_client(ScriptedProvider(ok(text)))
    assert client.complete("m", REQUEST).parsed == Answer(score=3)


def test_truncated_answer_is_its_own_status() -> None:
    client, _ = make_client(ScriptedProvider(ok('{"sco', finish_reason="length")))
    result = client.complete("m", REQUEST)
    assert result.status is CallStatus.TRUNCATED
    assert result.output_tokens == 20


def test_refusal_is_its_own_status() -> None:
    client, _ = make_client(ScriptedProvider(ok("", finish_reason="content_filter")))
    assert client.complete("m", REQUEST).status is CallStatus.REFUSED


def test_temporary_failures_are_retried_with_backoff() -> None:
    provider = ScriptedProvider(
        ProviderError(CallStatus.RATE_LIMITED, "slow down", retryable=True),
        ProviderError(CallStatus.TIMEOUT, "too slow", retryable=True),
        ok(),
    )
    client, sleeps = make_client(provider)
    result = client.complete("m", REQUEST)
    assert result.status is CallStatus.OK
    assert result.attempts == 3
    assert len(sleeps) == 2


def test_retries_stop_at_max_retries() -> None:
    errors = [ProviderError(CallStatus.TIMEOUT, "too slow", retryable=True) for _ in range(3)]
    provider = ScriptedProvider(*errors)
    client, _ = make_client(provider)
    result = client.complete("m", REQUEST)
    assert result.status is CallStatus.TIMEOUT
    assert result.attempts == 3  # the first try plus max_retries=2
    assert result.error == "too slow"


def test_permanent_failures_are_not_retried() -> None:
    provider = ScriptedProvider(ProviderError(CallStatus.AUTH_ERROR, "bad key"))
    client, sleeps = make_client(provider)
    result = client.complete("m", REQUEST)
    assert (result.status, result.attempts, sleeps) == (CallStatus.AUTH_ERROR, 1, [])


def test_prompt_too_big_for_the_context_window_is_not_sent() -> None:
    provider = ScriptedProvider()
    client, _ = make_client(provider)
    big = LLMRequest(
        messages=(Message("user", "x" * 12_000),), output_schema=Answer, prompt_version="t"
    )
    result = client.complete("m", big)
    assert result.status is CallStatus.CONTEXT_OVERFLOW
    assert provider.calls == []


def test_paid_model_without_a_spending_cap_is_not_called() -> None:
    provider = ScriptedProvider()
    client, _ = make_client(provider, cap=None, paid=PAID)
    result = client.complete("paid", REQUEST)
    assert result.status is CallStatus.BUDGET_EXHAUSTED
    assert "max_usd_total" in (result.error or "")
    assert provider.calls == []


def test_call_that_could_pass_the_cap_is_not_sent() -> None:
    provider = ScriptedProvider()
    # The worst case is the full max_output_tokens: 1000 x $10/M = 10,000 micro-dollars.
    client, _ = make_client(provider, cap=1.0, spent=995_000, paid=PAID)
    assert client.complete("paid", REQUEST).status is CallStatus.BUDGET_EXHAUSTED
    assert provider.calls == []


def test_budget_is_checked_again_before_each_retry() -> None:
    # Worst case per attempt: (prompt estimate + 1000 overhead) x $2/M + 1000 x $10/M,
    # a little over 12,000 micro-dollars. The cap leaves room for one attempt only.
    provider = ScriptedProvider(
        ProviderError(CallStatus.TIMEOUT, "too slow", retryable=True),
        ok(),
    )
    client, _ = make_client(provider, cap=0.02, paid=PAID)
    result = client.complete("paid", REQUEST)
    assert result.status is CallStatus.BUDGET_EXHAUSTED
    assert result.attempts == 1
    assert len(provider.calls) == 1


def test_timed_out_cloud_attempt_counts_at_its_worst_case() -> None:
    provider = ScriptedProvider(
        ProviderError(CallStatus.TIMEOUT, "too slow", retryable=True),
        ok(),
    )
    client, _ = make_client(provider, cap=1.0, paid=PAID)
    result = client.complete("paid", REQUEST)
    assert result.status is CallStatus.OK
    assert result.attempts == 2
    assert result.cost_micros > 12_000 + 400  # the timeout's worst case plus the answer
    assert client.spent_micros == result.cost_micros


def test_rate_limited_attempt_costs_nothing() -> None:
    provider = ScriptedProvider(
        ProviderError(CallStatus.RATE_LIMITED, "slow down", retryable=True),
        ok(),
    )
    client, _ = make_client(provider, cap=1.0, paid=PAID)
    assert client.complete("paid", REQUEST).cost_micros == 400


def test_unexpected_provider_exception_becomes_a_status() -> None:
    class BrokenProvider:
        def complete(self, *args: Any) -> Completion:
            raise RuntimeError("bug in an adapter")

    client = LLMClient(LLMConfig(models={"m": LOCAL}), BrokenProvider())
    result = client.complete("m", REQUEST)
    assert result.status is CallStatus.PROVIDER_ERROR
    assert result.error == "unexpected RuntimeError"


def test_local_models_ignore_the_cap() -> None:
    client, _ = make_client(ScriptedProvider(ok()), cap=None)
    result = client.complete("m", REQUEST)
    assert (result.status, result.cost_micros) == (CallStatus.OK, 0)


def test_missing_usage_is_estimated_and_flagged() -> None:
    client, _ = make_client(ScriptedProvider(ok(input_tokens=None, output_tokens=None)))
    result = client.complete("m", REQUEST)
    assert result.tokens_estimated
    assert result.input_tokens > 0 and result.output_tokens > 0


def test_unknown_model_name() -> None:
    client, _ = make_client(ScriptedProvider())
    with pytest.raises(UnknownModelError):
        client.complete("nope", REQUEST)


def test_logs_have_counts_but_no_prompt_or_answer_text(caplog: pytest.LogCaptureFixture) -> None:
    secret = "UNIQUE-SYNTHETIC-RESUME-LINE"
    request = LLMRequest(
        messages=(Message("user", secret),), output_schema=Answer, prompt_version="test/v1"
    )
    client, _ = make_client(ScriptedProvider(ok(f'{{"score": 4, "note": "{secret}"}}')))
    with caplog.at_level("INFO", logger="jobsearch"):
        client.complete("m", request)
    [record] = [r for r in caplog.records if r.getMessage() == "llm call"]
    assert getattr(record, "status", None) == "invalid_output"
    assert getattr(record, "prompt_version", None) == "test/v1"
    assert all(secret not in str(vars(r)) for r in caplog.records)


def test_cost_rounds_up_to_whole_micro_dollars() -> None:
    assert cost_micros(PAID, 1, 0) == 2
    haiku_like = PAID.model_copy(update={"price_per_mtok": ModelPrice(input=0.1, output=0.5)})
    assert cost_micros(haiku_like, 1, 1) == 1  # 0.6 micro-dollars rounds up


def test_token_estimate_is_on_the_high_side() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 2  # about 3 characters per token


def test_parse_output_error_never_quotes_the_answer() -> None:
    with pytest.raises(OutputParseError) as caught:
        parse_output('{"score": "PRIVATE-TEXT"}', Answer)
    assert "PRIVATE-TEXT" not in str(caught.value)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"local": True, "api_base": "https://api.example.com"}, "localhost"),
        ({"local": True}, "localhost"),
        (
            {"local": True, "api_base": "http://localhost:11434", "api_key_env": "K"},
            "api_key_env",
        ),
        ({}, "api_key_env"),
        ({"api_key_env": "K"}, "price_per_mtok"),
        (
            {
                "local": True,
                "api_base": "http://localhost:11434",
                "params": {"base_url": "https://elsewhere.example.com", "api_key": "x"},
            },
            "api_key, base_url",
        ),
        (
            {"api_key_env": "K", "max_output_tokens": 16000, "price_per_mtok": FREE},
            "smaller than context_window",
        ),
        (
            {"local": True, "api_base": "http://127.0.0.1:11434", "params": {"num_ctx": 2048}},
            "num_ctx",
        ),
    ],
)
def test_model_config_rejects_unsafe_or_inconsistent_settings(
    fields: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ModelConfig(litellm_model="x/model", context_window=16000, **fields)


def test_cloud_model_may_state_a_zero_price_explicitly() -> None:
    model = ModelConfig(
        litellm_model="x/free-model", api_key_env="K", context_window=16000, price_per_mtok=FREE
    )
    assert model.price_per_mtok == FREE


def test_spend_cap_has_no_default() -> None:
    assert LLMConfig().max_usd_total is None
    assert LLMConfig().models == {}
