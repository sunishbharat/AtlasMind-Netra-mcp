"""AgendaDecomposer: LLM mocked via TestModel/FunctionModel (no live network or LLM)."""

from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from core.agenda_decomposer import AgendaDecomposer, load_agenda_decomposer
from core.exceptions import ConfigError, DecompositionError
from models.responses import AgendaDecomposition, AgendaTopic

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO_ROOT / "prompts" / "agenda_decomposition_prompt.md"

_TWO_TOPICS = AgendaDecomposition(
    topics=[
        AgendaTopic(
            topic_id="topic_1",
            description="Top blockers - Carline XX",
            suggested_query="top blockers hampering production of carline XX",
            projects=["CAR"],
        ),
        AgendaTopic(
            topic_id="topic_2",
            description="Open criticals - BOM module",
            suggested_query="open critical issues in BOM module",
            projects=["BOM"],
        ),
    ]
)


def _make_decomposer(model: object) -> AgendaDecomposer:
    agent: Agent[None, AgendaDecomposition] = Agent(  # type: ignore[call-overload]
        model,
        output_type=AgendaDecomposition,
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
    )
    return AgendaDecomposer(agent=agent)


def _function_model_returning(output: AgendaDecomposition) -> FunctionModel:
    def handler(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(output.model_dump_json())])

    return FunctionModel(handler)


async def test_decompose_returns_topics() -> None:
    decomposer = _make_decomposer(_function_model_returning(_TWO_TOPICS))
    topics = await decomposer.decompose("1. Carline XX blockers\n2. BOM open criticals")
    assert len(topics) == 2
    assert topics[0].topic_id == "topic_1"
    assert topics[0].description == "Top blockers - Carline XX"
    assert topics[1].projects == ["BOM"]


async def test_decompose_caps_at_max_topics() -> None:
    eleven = AgendaDecomposition(
        topics=[
            AgendaTopic(
                topic_id=f"topic_{i}",
                description=f"Section {i}",
                suggested_query=f"query {i}",
            )
            for i in range(1, 12)
        ]
    )
    decomposer = _make_decomposer(_function_model_returning(eleven))
    topics = await decomposer.decompose("big agenda")
    assert len(topics) == 10


async def test_decompose_assigns_topic_ids_when_missing() -> None:
    no_ids = AgendaDecomposition(
        topics=[
            AgendaTopic(topic_id="", description="Desc A", suggested_query="query A"),
            AgendaTopic(topic_id="", description="Desc B", suggested_query="query B"),
        ]
    )
    decomposer = _make_decomposer(_function_model_returning(no_ids))
    topics = await decomposer.decompose("agenda")
    assert topics[0].topic_id == "topic_1"
    assert topics[1].topic_id == "topic_2"


async def test_decompose_preserves_explicit_topic_ids() -> None:
    decomposer = _make_decomposer(_function_model_returning(_TWO_TOPICS))
    topics = await decomposer.decompose("agenda")
    assert topics[0].topic_id == "topic_1"
    assert topics[1].topic_id == "topic_2"


async def test_decompose_with_project_scope_override() -> None:
    called_prompts: list[str] = []

    def handler(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        for msg in messages:
            for part in msg.parts:
                if hasattr(part, "content"):
                    called_prompts.append(str(part.content))
        return ModelResponse(parts=[TextPart(_TWO_TOPICS.model_dump_json())])

    decomposer = _make_decomposer(FunctionModel(handler))
    await decomposer.decompose("daily standup", projects=["CAR", "BOM"])
    assert any("CAR" in p and "BOM" in p for p in called_prompts)


async def test_decompose_raises_decomposition_error_on_agent_failure() -> None:
    def always_prose(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("I cannot help with this.")])

    decomposer = _make_decomposer(FunctionModel(always_prose))
    with pytest.raises(DecompositionError):
        await decomposer.decompose("agenda text")


def test_load_agenda_decomposer_raises_config_error_when_prompt_missing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "no_such_prompt.md"
    with pytest.raises(ConfigError, match="cannot read agenda decomposition prompt"):
        load_agenda_decomposer("test", missing)


def test_load_agenda_decomposer_succeeds_with_valid_prompt() -> None:
    decomposer = load_agenda_decomposer("test", PROMPT_PATH)
    assert isinstance(decomposer, AgendaDecomposer)
