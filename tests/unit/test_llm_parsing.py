"""Reading a model's answer: unwrap thinking and code fences, then validate strictly."""

import pytest
from pydantic import BaseModel, ConfigDict, Field

from jobsearch.llm.parsing import OutputParseError, parse_output, strip_wrappers


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)


def test_plain_json_is_parsed() -> None:
    assert parse_output('{"score": 4}', Answer) == Answer(score=4)


def test_thinking_and_code_fences_are_removed() -> None:
    text = '<think>The job wants Python.\n{"score": 1}</think>\n```json\n{"score": 3}\n```'
    assert strip_wrappers(text) == '{"score": 3}'
    assert parse_output(text, Answer) == Answer(score=3)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("Sure! The score is 4.", "not valid JSON"),
        ('["score", 4]', "expected a JSON object"),
        ('{"score": 9}', "schema error"),
        ('{"score": 4, "extra": true}', "schema error"),
        ('Here you go: {"score": 4}', "not valid JSON"),  # never dug out of prose
    ],
)
def test_anything_else_is_rejected_not_repaired(text: str, error: str) -> None:
    with pytest.raises(OutputParseError, match=error):
        parse_output(text, Answer)


def test_parse_output_error_never_quotes_the_answer() -> None:
    with pytest.raises(OutputParseError) as caught:
        parse_output('{"score": "PRIVATE-TEXT"}', Answer)
    assert "PRIVATE-TEXT" not in str(caught.value)
