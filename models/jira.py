"""Typed models for Jira instance metadata read from the backend's on-disk cache."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JiraField(BaseModel):
    """One entry of jira_fields.json (Jira GET /rest/api/2/field shape)."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    id: str
    name: str
    custom: bool = False
    # Any: raw Jira schema blob, only echoed into the clarifier prompt, never interpreted.
    # Aliased because "schema" shadows a deprecated BaseModel attribute in pydantic v2.
    field_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    description: str | None = None
