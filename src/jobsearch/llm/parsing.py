"""Turning a model's answer text into a validated object.

Models wrap JSON in different ways: reasoning models may put their thinking in
<think>...</think> first, and some add a ```json code fence. Those wrappers are
removed; anything else that isn't valid JSON for the schema is rejected, never
repaired.
"""

import json
import re

from pydantic import BaseModel, ValidationError

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*\n?(.*?)\n?```$", re.DOTALL)


class OutputParseError(ValueError):
    """The answer isn't a JSON object matching the schema. The message never quotes it."""


def strip_wrappers(text: str) -> str:
    text = _THINK_BLOCK.sub("", text).strip()
    if fenced := _FENCE.match(text):
        text = fenced[1].strip()
    return text


def parse_output[T: BaseModel](text: str, schema: type[T]) -> T:
    body = strip_wrappers(text)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise OutputParseError(f"not valid JSON (line {exc.lineno}, column {exc.colno})") from exc
    if not isinstance(data, dict):
        raise OutputParseError(f"expected a JSON object, got {type(data).__name__}")
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "(root)"
        raise OutputParseError(
            f"{exc.error_count()} schema error(s), first at {location}: {first['msg']}"
        ) from exc
