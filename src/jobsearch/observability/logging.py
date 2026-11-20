"""Structured JSON logging to a local file. Log IDs and statuses, never resume or page text."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "run_id": self._run_id,
            "component": record.name,
            "message": record.getMessage(),
        }
        entry.update(
            (key, value) for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS
        )
        if record.exc_info and record.exc_info[0] is not None:
            # The exception type only: tracebacks can quote fetched content.
            entry["exception"] = record.exc_info[0].__name__
        return json.dumps(entry, default=str)


def configure_logging(log_file: Path, run_id: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(JsonFormatter(run_id))
    root = logging.getLogger("jobsearch")
    root.handlers = [handler]
    root.setLevel(logging.INFO)
    root.propagate = False
