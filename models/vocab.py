"""Typed model for the disambiguation vocabulary (config/clarification_vocab.json)."""

from pydantic import BaseModel, ConfigDict, Field


class VocabEntry(BaseModel):
    """Disambiguation knowledge for one ambiguous term (design doc: clarification engine)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ambiguity: str = Field(description="Why the term is ambiguous in Jira's domain.")
    questions: list[str] = Field(
        default_factory=list,
        description="Question templates the clarifier may adapt; phrased to require "
        "team-specific knowledge.",
    )
    jql_patterns: dict[str, str] = Field(
        default_factory=dict,
        description="Interpretation key -> JQL fragment, e.g. 'label' -> 'labels = escalation'.",
    )
