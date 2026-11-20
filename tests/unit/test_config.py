from pathlib import Path

import pytest
import yaml

from jobsearch.config import load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_loads_unique_keys(tmp_path: Path) -> None:
    config = load_config(
        write(tmp_path, "ingestion:\n  fetch:\n    max_retries: 1\n    timeout_seconds: 5\n")
    )
    assert config.ingestion.fetch.max_retries == 1
    assert config.ingestion.fetch.timeout_seconds == 5


@pytest.mark.parametrize(
    "text",
    [
        "logging:\n  file: a.log\nlogging:\n  file: b.log\n",
        "ingestion:\n  fetch:\n    max_retries: 1\n    max_retries: 2\n",
        "ingestion:\n  fetch:\n    max_retries: 1\n  fetch:\n    timeout_seconds: 5\n",
    ],
    ids=["top_level", "nested_value", "nested_section"],
)
def test_rejects_duplicate_keys(tmp_path: Path, text: str) -> None:
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        load_config(write(tmp_path, text))
