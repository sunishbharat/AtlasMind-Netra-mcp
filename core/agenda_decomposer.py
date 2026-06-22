"""Agenda text to AgendaTopic list (design doc: AgendaDecomposer).

One PydanticAI call turns a free-text meeting agenda into a structured list of
data questions. Each question becomes one section in the briefing report.
"""

from pathlib import Path
from typing import Protocol

import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.models import Model

from core.exceptions import ConfigError, DecompositionError
from models.responses import AgendaDecomposition, AgendaTopic

logger = structlog.get_logger(__name__)


class AgendaDecomposerPort(Protocol):
    """Decomposition seam for the briefing pipeline (injectable in tests)."""

    async def decompose(
        self,
        agenda_text: str,
        projects: list[str] | None = None,
    ) -> list[AgendaTopic]: ...


class AgendaDecomposer:
    """Runs one PydanticAI call to convert agenda text into AgendaTopic list.

    The agent is injected already-constructed so tests can pass TestModel/FunctionModel
    without a live LLM (same pattern as Clarifier and IssueAnalyser).
    """

    def __init__(self, agent: Agent[None, AgendaDecomposition], max_topics: int = 10) -> None:
        self._agent = agent
        self._max_topics = max_topics

    async def decompose(
        self,
        agenda_text: str,
        projects: list[str] | None = None,
    ) -> list[AgendaTopic]:
        """Parse agenda_text and return up to max_topics data questions."""
        prompt = _build_prompt(agenda_text, projects)
        try:
            result = await self._agent.run(prompt)
            topics = result.output.topics
        except (AgentRunError, UserError) as exc:
            raise DecompositionError(f"agenda decomposition LLM call failed: {exc}") from exc

        assigned: list[AgendaTopic] = []
        for i, topic in enumerate(topics[: self._max_topics]):
            if not topic.topic_id:
                topic = topic.model_copy(update={"topic_id": f"topic_{i + 1}"})
            assigned.append(topic)

        logger.info("agenda_decomposed", topic_count=len(assigned))
        return assigned


def load_agenda_decomposer(
    model: Model | str,
    prompt_path: Path,
    retries: int = 2,
    max_topics: int = 10,
) -> AgendaDecomposer:
    """Read the prompt file and construct an AgendaDecomposer.

    Raises ConfigError when the prompt file is missing or unreadable - caught at startup.
    """
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"{prompt_path}: cannot read agenda decomposition prompt: {exc}") from exc
    agent: Agent[None, AgendaDecomposition] = Agent(
        model,
        output_type=AgendaDecomposition,
        system_prompt=system_prompt,
        retries=retries,
        model_settings={"bedrock_cache_instructions": True},
    )
    return AgendaDecomposer(agent=agent, max_topics=max_topics)


def _build_prompt(agenda_text: str, projects: list[str] | None) -> str:
    lines = ["AGENDA TEXT:", agenda_text.strip()]
    if projects:
        lines += ["", f"PROJECT SCOPE OVERRIDE: {', '.join(projects)}"]
    lines += ["", "-> Decompose the agenda into data questions per the system prompt."]
    return "\n".join(lines)
