"""LLMClient: the one way the app calls a model.

For every call it:
1. estimates the prompt's size and refuses to send one that wouldn't fit the model's
   context window (some runtimes silently cut long prompts instead of failing);
2. for cloud models, refuses an attempt that could take spend past `llm.max_usd_total`,
   checked again before every retry;
3. retries temporary failures (timeouts, rate limits, server errors) with backoff;
4. records latency, tokens and cost; a timed-out cloud attempt counts at its worst-case
   cost, because the provider may have processed and billed it;
5. validates the answer against the requested schema.
Whatever goes wrong with the call becomes an `LLMResult` status, never an exception.
The exception is a programming error: an unknown model name raises UnknownModelError.
"""

import json
import logging
import math
import random
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from pydantic import BaseModel

from jobsearch.config import LLMConfig, ModelConfig
from jobsearch.llm.parsing import OutputParseError, parse_output
from jobsearch.llm.providers.base import Completion, Provider, ProviderError
from jobsearch.llm.types import CallStatus, LLMRequest, LLMResult, Message
from jobsearch.paths import PROMPTS_DIR

SCHEMA_INSTRUCTION_FILE = PROMPTS_DIR / "llm" / "json_schema_instruction_v1.md"
# A deliberately high estimate of tokens per character (about 3 characters per token;
# English averages closer to 4). Used only before the call, for the context-window and
# budget checks, so an estimate that's too high is the safe direction.
_CHARS_PER_TOKEN = 3
# Providers add tokens of their own to a prompt (message formatting, the scaffolding for
# structured output), so the budget check reserves this many more input tokens.
_PROVIDER_OVERHEAD_TOKENS = 1000

logger = logging.getLogger(__name__)


class UnknownModelError(KeyError):
    pass


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / _CHARS_PER_TOKEN)


def cost_micros(model: ModelConfig, input_tokens: int, output_tokens: int) -> int:
    """Price per million tokens in dollars is exactly micro-dollars per token."""
    price = model.price_per_mtok
    return math.ceil(input_tokens * price.input + output_tokens * price.output)


class LLMClient:
    def __init__(
        self,
        config: LLMConfig,
        provider: Provider,
        *,
        spent_before_micros: int = 0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._provider = provider
        self._spent_micros = spent_before_micros
        self._sleep = sleep
        self._clock = clock

    @property
    def spent_micros(self) -> int:
        """Spend so far: what was spent before this client, plus its own calls."""
        return self._spent_micros

    def model(self, alias: str) -> ModelConfig:
        try:
            return self._config.models[alias]
        except KeyError:
            raise UnknownModelError(alias) from None

    def complete[T: BaseModel](self, alias: str, request: LLMRequest[T]) -> LLMResult[T]:
        model = self.model(alias)
        schema = request.output_schema.model_json_schema()
        messages, json_schema = _prepare(model, request.messages, schema)
        estimated_input = sum(estimate_tokens(m.content) for m in messages)
        if json_schema is not None:
            estimated_input += estimate_tokens(json.dumps(json_schema))

        if estimated_input + model.max_output_tokens > model.context_window:
            return self._finish(
                LLMResult(
                    alias,
                    CallStatus.CONTEXT_OVERFLOW,
                    error=(
                        f"prompt is about {estimated_input} tokens; with "
                        f"{model.max_output_tokens} for the answer that exceeds the "
                        f"{model.context_window}-token context window"
                    ),
                ),
                request,
            )
        worst_case = self._worst_case_micros(model, estimated_input)
        charged = 0  # assumed cost of timed-out attempts
        attempt = 0
        while True:
            if refusal := self._budget_refusal(model, worst_case):
                return self._finish(
                    LLMResult(
                        alias,
                        CallStatus.BUDGET_EXHAUSTED,
                        attempts=attempt,
                        cost_micros=charged,
                        error=refusal,
                    ),
                    request,
                )
            attempt += 1
            started = self._clock()
            try:
                completion = self._provider.complete(model, messages, json_schema)
            except ProviderError as exc:
                failure = exc
            except Exception as exc:  # A provider bug must not end a whole run.
                failure = ProviderError(
                    CallStatus.PROVIDER_ERROR, f"unexpected {type(exc).__name__}"
                )
            else:
                latency_ms = round((self._clock() - started) * 1000)
                result = self._result(
                    alias, model, request, completion, estimated_input, latency_ms, attempt
                )
                if charged:
                    result = replace(result, cost_micros=result.cost_micros + charged)
                return self._finish(result, request)
            latency_ms = round((self._clock() - started) * 1000)
            if failure.status is CallStatus.TIMEOUT and worst_case:
                charged += worst_case
                self._spent_micros += worst_case
            if not failure.retryable or attempt > self._config.max_retries:
                return self._finish(
                    LLMResult(
                        alias,
                        failure.status,
                        latency_ms=latency_ms,
                        attempts=attempt,
                        cost_micros=charged,
                        error=str(failure),
                    ),
                    request,
                )
            self._sleep(self._backoff(attempt - 1))

    def _worst_case_micros(self, model: ModelConfig, estimated_input: int) -> int:
        """The most one attempt could cost: 0 for local models."""
        if model.local:
            return 0
        tokens_in = estimated_input + _PROVIDER_OVERHEAD_TOKENS
        return cost_micros(model, tokens_in, model.max_output_tokens)

    def _budget_refusal(self, model: ModelConfig, worst_case: int) -> str | None:
        if model.local:
            return None
        cap = self._config.max_usd_total
        if cap is None:
            return "llm.max_usd_total isn't set; cloud models aren't called without a spending cap"
        if self._spent_micros + worst_case > round(cap * 1_000_000):
            return (
                f"could cost up to ${worst_case / 1e6:.4f}; spent ${self._spent_micros / 1e6:.4f} "
                f"of the ${cap:.2f} cap"
            )
        return None

    def _result[T: BaseModel](
        self,
        alias: str,
        model: ModelConfig,
        request: LLMRequest[T],
        completion: Completion,
        estimated_input: int,
        latency_ms: int,
        attempts: int,
    ) -> LLMResult[T]:
        estimated = completion.input_tokens is None or completion.output_tokens is None
        input_tokens = completion.input_tokens
        if input_tokens is None:
            input_tokens = estimated_input
        output_tokens = completion.output_tokens
        if output_tokens is None:
            output_tokens = estimate_tokens(completion.text)
        cost = cost_micros(model, input_tokens, output_tokens)
        self._spent_micros += cost

        status, parsed, error = CallStatus.OK, None, None
        if completion.finish_reason == "length":
            status, error = CallStatus.TRUNCATED, "the answer hit max_output_tokens"
        elif completion.finish_reason in ("content_filter", "refusal"):
            status, error = CallStatus.REFUSED, "the model declined to answer"
        else:
            try:
                parsed = parse_output(completion.text, request.output_schema)
            except OutputParseError as exc:
                status, error = CallStatus.INVALID_OUTPUT, str(exc)
        return LLMResult(
            alias,
            status,
            parsed=parsed,
            text=completion.text,
            served_model=completion.served_model,
            finish_reason=completion.finish_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_estimated=estimated,
            latency_ms=latency_ms,
            cost_micros=cost,
            attempts=attempts,
            error=error,
        )

    def _finish[T: BaseModel](self, result: LLMResult[T], request: LLMRequest[T]) -> LLMResult[T]:
        # Counts and statuses only: prompts and answers can contain resume text.
        logger.info(
            "llm call",
            extra={
                "model_alias": result.model_alias,
                "prompt_version": request.prompt_version,
                "served_model": result.served_model,
                "status": result.status.value,
                "latency_ms": result.latency_ms,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "cost_micros": result.cost_micros,
                "attempts": result.attempts,
                "finish_reason": result.finish_reason,
            },
        )
        return result

    def _backoff(self, attempt: int) -> float:
        base = self._config.backoff_base_seconds
        return float(base * 2**attempt + random.uniform(0, base))


def _prepare(
    model: ModelConfig, messages: tuple[Message, ...], schema: dict[str, Any]
) -> tuple[tuple[Message, ...], dict[str, Any] | None]:
    """Native structured output sends the schema separately; otherwise it joins the prompt."""
    if model.structured_output == "json_schema":
        return messages, schema
    instruction = SCHEMA_INSTRUCTION_FILE.read_text(encoding="utf-8").replace(
        "{schema}", json.dumps(schema, indent=2)
    )
    return (*messages, Message("user", instruction)), None
