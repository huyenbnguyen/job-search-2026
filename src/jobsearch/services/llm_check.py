"""`jobsearch llm check`: try a configured model with made-up data before real use.

The check sends a short synthetic job and resume (never yours) and asks for a small
structured answer. A model passes when it answers with valid JSON for the schema, which
shows that it's reachable, the key works, structured output works, and that token
counts and cost are being recorded.
"""

from pydantic import BaseModel, ConfigDict, Field

from jobsearch.llm.client import LLMClient
from jobsearch.llm.types import LLMRequest, LLMResult, Message
from jobsearch.paths import PROMPTS_DIR

CHECK_PROMPT_FILE = PROMPTS_DIR / "llm_check" / "check_v1.md"


class CheckAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched_skills: list[str]
    fit_score: int = Field(ge=1, le=5)


def check_request() -> LLMRequest[CheckAnswer]:
    prompt = CHECK_PROMPT_FILE.read_text(encoding="utf-8")
    return LLMRequest(messages=(Message("user", prompt),), output_schema=CheckAnswer)


def run_check(client: LLMClient, alias: str) -> LLMResult[CheckAnswer]:
    return client.complete(alias, check_request())
