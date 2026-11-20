"""`jobsearch llm list` and `llm check`, using LiteLLM's mock responses: no network."""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from jobsearch.cli import app

GOOD = json.dumps({"matched_skills": ["Python", "SQL"], "fit_score": 3})


def write_config(tmp_path: Path, models: dict[str, Any], cap: float | None = 1.0) -> Path:
    llm: dict[str, Any] = {"models": models, "max_retries": 0}
    if cap is not None:
        llm["max_usd_total"] = cap
    config = {"llm": llm, "logging": {"file": str(tmp_path / "log.jsonl")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def local(mock: str = GOOD) -> dict[str, Any]:
    return {
        "litellm_model": "ollama_chat/test-model",
        "local": True,
        "api_base": "http://localhost:11434",
        "context_window": 16384,
        "max_output_tokens": 2000,
        "params": {"mock_response": mock},
    }


def cloud(mock: str = GOOD) -> dict[str, Any]:
    return {
        "litellm_model": "anthropic/claude-haiku-5-5",
        "api_key_env": "TEST_ANTHROPIC_KEY",
        "context_window": 100_000,
        "max_output_tokens": 2000,
        "price_per_mtok": {"input": 0.1, "output": 0.5},
        "params": {"mock_response": mock},
    }


@pytest.fixture(autouse=True)
def wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # Rich wraps table cells to the terminal width, which would split words.
    monkeypatch.setenv("COLUMNS", "300")


def invoke(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def flat(text: str) -> str:
    return " ".join(text.split())


def test_list_shows_models_key_status_and_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)
    path = write_config(tmp_path, {"qwen": local(), "haiku": cloud()}, cap=20)
    result = invoke("llm", "list", "--config", str(path))
    assert result.exit_code == 0, result.output
    out = flat(result.output)
    assert "qwen" in out and "haiku" in out
    assert "not needed" in out
    assert "missing: TEST_ANTHROPIC_KEY" in out
    assert "$20.00" in out


def test_check_passes_and_reports_latency_tokens_and_cost(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
    path = write_config(tmp_path, {"qwen": local(), "haiku": cloud()})
    result = invoke("llm", "check", "--all", "--config", str(path))
    assert result.exit_code == 0, result.output
    out = flat(result.output)
    assert "2 of 2 passed" in out
    assert "fit 3/5" in out
    assert "Total cost: $0.0000" in out or "Total cost: $0.000" in out
    log = [json.loads(line) for line in (tmp_path / "log.jsonl").read_text().splitlines()]
    calls = [entry for entry in log if entry["message"] == "llm call"]
    assert {c["model_alias"] for c in calls} == {"qwen", "haiku"}
    assert all("latency_ms" in c and "cost_micros" in c for c in calls)
    assert all("Sam Example" not in json.dumps(c) for c in log)


def test_check_fails_on_invalid_output(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"qwen": local("I think it's a 3.")})
    result = invoke("llm", "check", "qwen", "--config", str(path))
    assert result.exit_code == 1
    out = flat(result.output)
    assert "invalid_output" in out and "0 of 1 passed" in out


def test_check_refuses_paid_model_without_a_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "sk-test")
    path = write_config(tmp_path, {"haiku": cloud()}, cap=None)
    result = invoke("llm", "check", "haiku", "--config", str(path))
    assert result.exit_code == 1
    assert "budget_exhausted" in flat(result.output)


def test_check_reports_a_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)
    path = write_config(tmp_path, {"haiku": cloud()})
    result = invoke("llm", "check", "haiku", "--config", str(path))
    assert result.exit_code == 1
    out = flat(result.output)
    assert "not_configured" in out and "TEST_ANTHROPIC_KEY" in out


def test_check_needs_model_names(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"qwen": local()})
    assert "Name the models" in flat(invoke("llm", "check", "--config", str(path)).output)
    result = invoke("llm", "check", "nope", "--config", str(path))
    assert result.exit_code == 1
    assert "Unknown model: nope" in flat(result.output)


def test_example_config_loads() -> None:
    from jobsearch.config import load_config

    config = load_config(Path("config/config.example.yaml"))
    assert config.llm.max_usd_total == 20
    assert {"sonnet", "haiku", "qwen3-14b", "gpt-oss-20b"} <= set(config.llm.models)
    assert all(m.local for name, m in config.llm.models.items() if "qwen" in name or "gpt" in name)
