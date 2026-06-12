"""Clarifier: typed PydanticAI calls faked via TestModel/FunctionModel (no live LLM)."""

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from core.clarifier import Clarifier
from core.exceptions import ClarificationError, ConfigError
from core.vocab_lookup import VocabLookup
from tests.conftest import PROMPT_PATH


async def test_formulate_question_returns_typed_output(vocab: VocabLookup) -> None:
    model = TestModel(
        custom_output_args={
            "question": "Does your team use label=escalation or priority=Critical?",
            "terms": ["escalation"],
        }
    )
    clarifier = Clarifier(model=model, prompt_path=PROMPT_PATH)
    out = await clarifier.formulate_question(
        query="show escalations",
        terms=["escalation"],
        vocab=vocab.entries_for(["escalation"]),
        conventions=[],
        fields=[],
    )
    assert out.question.startswith("Does your team")
    assert out.terms == ["escalation"]


async def test_resolve_answer_returns_typed_resolutions(vocab: VocabLookup) -> None:
    model = TestModel(
        custom_output_args={
            "resolutions": [
                {
                    "term": "escalation",
                    "resolution_key": "label",
                    "jql_hint": "labels = escalation",
                }
            ]
        }
    )
    clarifier = Clarifier(model=model, prompt_path=PROMPT_PATH)
    out = await clarifier.resolve_answer(
        query="show escalations",
        terms=["escalation"],
        answer="we use the escalation label",
        vocab=vocab.entries_for(["escalation"]),
    )
    assert out.resolutions[0].resolution_key == "label"
    assert out.resolutions[0].jql_hint == "labels = escalation"


async def test_unusable_model_output_raises_clarification_error(vocab: VocabLookup) -> None:
    def always_prose(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="I cannot answer in JSON, sorry")])

    clarifier = Clarifier(model=FunctionModel(always_prose), prompt_path=PROMPT_PATH, retries=1)
    with pytest.raises(ClarificationError, match="failed to formulate question"):
        await clarifier.formulate_question(
            query="show escalations",
            terms=["escalation"],
            vocab=vocab.entries_for(["escalation"]),
            conventions=[],
            fields=[],
        )


def test_missing_prompt_file_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="clarification prompt"):
        Clarifier(model=TestModel(), prompt_path=tmp_path / "missing.md")
