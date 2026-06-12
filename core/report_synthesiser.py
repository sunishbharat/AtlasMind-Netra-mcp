"""Markdown report builder (design doc: ReportSynthesiser).

Milestone 1a scope: one human-verifiable report per dispatched query, so every output can
be reviewed by a person. Briefing sections (ranked blockers, citations, multi-topic
reports) extend this class in Milestone 3.

Trust rules honoured: facts come straight from the backend payload; applied
interpretations are listed so a wrong assumption is caught in the output, not weeks later.
"""

from datetime import UTC, datetime
from typing import Any

from models.responses import QueryResponse

_MAX_DEFAULT_COLUMNS = 8


class ReportSynthesiser:
    """Turns a QueryResponse into a markdown document (no rendering, text only)."""

    def __init__(self, max_issues: int = 50) -> None:
        self._max_issues = max_issues

    def build_query_report(
        self,
        *,
        query: str,
        response: QueryResponse,
        generated_at: datetime | None = None,
    ) -> str:
        timestamp = (generated_at or datetime.now(UTC)).isoformat(timespec="seconds")
        lines: list[str] = [
            "# AtlasMind query report",
            "",
            f"- Generated: {timestamp}",
            f"- Session: {response.session_id}",
            "",
            "## Query",
            "",
            f"> {_cell(query)}",
            "",
        ]
        if response.applied_conventions:
            lines += [
                "## Applied interpretations",
                "",
                "| Term | JQL meaning | Source |",
                "|---|---|---|",
            ]
            lines += [
                f"| {_cell(c.term)} | `{_cell(c.jql_hint)}` | {c.source} |"
                for c in response.applied_conventions
            ]
            lines.append("")
        if response.jql:
            lines += ["## Generated JQL", "", "```jql", response.jql, "```", ""]
        if response.answer:
            lines += ["## Answer", "", _cell(response.answer), ""]

        lines += [f"## Issues ({response.shown} shown of {response.total} total)", ""]
        if response.issues:
            lines += self._issues_table(response)
        else:
            lines.append("(no issues returned)")
        lines.append("")

        if response.chart_spec is not None:
            spec = response.chart_spec
            lines += [
                "## Chart specification",
                "",
                f"- type: {spec.type}",
                f"- x: {spec.x_field}",
                f"- y: {spec.y_field}",
                f"- color: {spec.color_field}",
                f"- title: {spec.title}",
                f"- rendered in browser UI: {'yes' if response.ui_injected else 'no'}",
                "",
            ]
        if response.errors:
            lines += ["## Warnings", "", *[f"- {_cell(e)}" for e in response.errors], ""]

        lines += [
            "---",
            "",
            "Facts above come from Jira via the AtlasMind backend. Applied interpretations "
            "are listed so a wrong assumption is caught here (design doc trust rules).",
        ]
        return "\n".join(lines) + "\n"

    def _issues_table(self, response: QueryResponse) -> list[str]:
        columns = response.display_fields or _default_columns(response.issues[0])
        table = [
            "| " + " | ".join(_cell(c) for c in columns) + " |",
            "|" + "---|" * len(columns),
        ]
        table += [
            "| " + " | ".join(_cell(_issue_value(issue, c)) for c in columns) + " |"
            for issue in response.issues[: self._max_issues]
        ]
        omitted = len(response.issues) - self._max_issues
        if omitted > 0:
            table += ["", f"({omitted} more rows omitted)"]
        return table


def _default_columns(issue: dict[str, Any]) -> list[str]:
    return list(issue)[:_MAX_DEFAULT_COLUMNS]


def _issue_value(issue: dict[str, Any], column: str) -> object:
    """Map a display column to an issue key (contract: lowercased, underscored)."""
    for candidate in (column, column.lower(), column.lower().replace(" ", "_")):
        if candidate in issue:
            return issue[candidate]
    return ""


def _cell(value: object) -> str:
    """One markdown table cell: single line, pipes escaped."""
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
