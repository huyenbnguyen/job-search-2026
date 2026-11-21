"""The llm config section: model entries are checked for unsafe or inconsistent settings."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from jobsearch.config import LLMConfig, ModelConfig, ModelPrice, load_config

FREE = ModelPrice(input=0, output=0)


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


def test_example_config_loads() -> None:
    config = load_config(Path("config/config.example.yaml"))
    assert config.llm.max_usd_total == 20
    assert {"sonnet", "haiku", "qwen3-14b", "gpt-oss-20b"} <= set(config.llm.models)
    assert all(m.local for name, m in config.llm.models.items() if "qwen" in name or "gpt" in name)
