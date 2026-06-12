"""Typed LLM outputs for the clarifier (PydanticAI output_type models).

These are the only shapes the clarifier LLM is allowed to produce; raw completions are
never parsed (Coding Guidelines Rule 2).
"""

from pydantic import BaseModel, ConfigDict, Field


class ClarificationNeeded(BaseModel):
    """One targeted clarification question covering the detected ambiguous terms."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(
        description="The single question to show the user verbatim. Must reference real "
        "Jira field names and be unanswerable without team-specific knowledge."
    )
    terms: list[str] = Field(
        default_factory=list, description="The ambiguous terms this question covers."
    )


class TermResolutionOutput(BaseModel):
    """The user's intended meaning of one ambiguous term, as concrete JQL."""

    model_config = ConfigDict(frozen=True)

    term: str = Field(description="The ambiguous term being resolved.")
    resolution_key: str = Field(
        description="The matching key from the term's jql_patterns, or 'custom' when the "
        "user's answer fits none of them."
    )
    jql_hint: str = Field(
        description="A valid JQL fragment expressing the meaning, e.g. 'labels = escalation'."
    )


class ResolvedTerms(BaseModel):
    """All term resolutions extracted from one user clarification answer."""

    model_config = ConfigDict(frozen=True)

    resolutions: list[TermResolutionOutput] = Field(default_factory=list)
