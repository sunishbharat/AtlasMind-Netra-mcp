"""LLM-backed clarification (design doc: Clarifier).

All LLM calls go through PydanticAI with a typed output_type; raw completions are never
parsed (Coding Guidelines Rule 2). PydanticAI failures are translated to ClarificationError
at this boundary.
"""

from pathlib import Path

import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.models import Model

from core.exceptions import ClarificationError, ConfigError
from memory.conventions_store import Convention
from models.clarification import ClarificationNeeded, ResolvedTerms
from models.jira import JiraField
from models.vocab import VocabEntry

logger = structlog.get_logger(__name__)


class Clarifier:
    """Formulates one targeted question and resolves the user's answer, both fully typed.

    The model is injected (a PydanticAI model string such as "groq:llama-3.3-70b-versatile",
    or a Model instance - tests pass TestModel/FunctionModel).
    """

    def __init__(self, model: Model | str, prompt_path: Path, retries: int = 2) -> None:
        try:
            system_prompt = prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"{prompt_path}: cannot read clarification prompt: {exc}") from exc
        self._question_agent = Agent(
            model, output_type=ClarificationNeeded, system_prompt=system_prompt, retries=retries
        )
        self._resolve_agent = Agent(
            model, output_type=ResolvedTerms, system_prompt=system_prompt, retries=retries
        )

    async def formulate_question(
        self,
        *,
        query: str,
        terms: list[str],
        vocab: dict[str, VocabEntry],
        conventions: list[Convention],
        fields: list[JiraField],
    ) -> ClarificationNeeded:
        """One targeted question covering `terms`, grounded in real field names."""
        prompt = "\n".join(
            [
                "OPERATION: FORMULATE QUESTION",
                _fields_block(fields),
                _conventions_block(conventions),
                f"AMBIGUOUS TERMS DETECTED: {', '.join(terms)}",
                _vocab_block(vocab),
                f"USER QUERY: {query}",
                "-> Ask one targeted question covering all detected terms.",
            ]
        )
        try:
            result = await self._question_agent.run(prompt)
        except (AgentRunError, UserError) as exc:
            raise ClarificationError(f"failed to formulate question: {exc}") from exc
        return result.output

    async def resolve_answer(
        self,
        *,
        query: str,
        terms: list[str],
        answer: str,
        vocab: dict[str, VocabEntry],
    ) -> ResolvedTerms:
        """Map each ambiguous term to a JQL interpretation based on the user's answer."""
        prompt = "\n".join(
            [
                "OPERATION: RESOLVE ANSWER",
                f"AMBIGUOUS TERMS: {', '.join(terms)}",
                _vocab_block(vocab),
                f"ORIGINAL QUERY: {query}",
                f"USER ANSWER: {answer}",
                "-> Resolve every listed term to a resolution_key and a JQL fragment.",
            ]
        )
        try:
            result = await self._resolve_agent.run(prompt)
        except (AgentRunError, UserError) as exc:
            raise ClarificationError(f"failed to resolve answer: {exc}") from exc
        return result.output


def _fields_block(fields: list[JiraField]) -> str:
    if not fields:
        return "JIRA FIELDS AVAILABLE: (metadata unavailable - use standard Jira field names)"
    lines = [f"- {field.name} ({field.id})" for field in fields]
    return "JIRA FIELDS AVAILABLE:\n" + "\n".join(lines)


def _conventions_block(conventions: list[Convention]) -> str:
    if not conventions:
        return "KNOWN TEAM CONVENTIONS (skip these): none"
    lines = [f"- {c.term} -> {c.jql_hint} (project: {c.project})" for c in conventions]
    return "KNOWN TEAM CONVENTIONS (skip these):\n" + "\n".join(lines)


def _vocab_block(vocab: dict[str, VocabEntry]) -> str:
    if not vocab:
        return "DISAMBIGUATION VOCAB: none"
    lines: list[str] = []
    for term, entry in vocab.items():
        patterns = "; ".join(f"{key}: {jql}" for key, jql in entry.jql_patterns.items())
        suffix = f" | patterns: {patterns}" if patterns else ""
        lines.append(f"- {term}: {entry.ambiguity}{suffix}")
    return "DISAMBIGUATION VOCAB:\n" + "\n".join(lines)
