"""Markdown report builder (design doc: ReportSynthesiser).

Builds human-verifiable markdown reports for every dispatched query and briefing.
Trust rules honoured: facts come straight from the backend payload; AI suggestions are
labelled explicitly; applied interpretations are listed so wrong assumptions are caught.
"""

from datetime import UTC, datetime
from typing import Any

import structlog

from models.responses import BlockerAnalysis, BriefingSection, QueryResponse

log = structlog.get_logger(__name__)

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

    def build_briefing_report(
        self,
        *,
        agenda_text: str,
        sections: list[BriefingSection],
        session_id: str,
        generated_at: datetime | None = None,
    ) -> str:
        """Assemble a multi-topic briefing markdown report with ranked issues and AI labels."""
        timestamp = (generated_at or datetime.now(UTC)).isoformat(timespec="seconds")
        lines: list[str] = [
            "# AtlasMind executive briefing",
            "",
            f"- Generated: {timestamp}",
            f"- Session: {session_id}",
            f"- Topics: {len(sections)}",
            "",
            "## Agenda",
            "",
            f"> {_cell(agenda_text[:500])}",
            "",
            "---",
            "",
        ]
        for section in sections:
            lines += self._section_lines(section)

        lines += [
            "---",
            "",
            "**AI SUGGESTION** fields (suggested_resolution, mitigation, risk_note) are "
            "LLM-generated and labelled as such. Facts (days_blocked, owner, priority, "
            "blocked_reason) are derived from Jira data. Citations reference Jira issue keys "
            "and comment IDs.",
        ]
        return "\n".join(lines) + "\n"

    def _section_lines(self, section: BriefingSection) -> list[str]:
        lines: list[str] = [
            f"## {section.description}",
            "",
            f"- Query: {_cell(section.query_used or '(none)')}",
            f"- JQL: `{_cell(section.jql or '(none)')}`",
            f"- Total found: {section.total_found}",
            f"- Top {len(section.top_issues)} shown",
            "",
        ]
        if section.errors:
            lines += ["**Warnings:**", *[f"- {_cell(e)}" for e in section.errors], ""]

        if not section.top_issues:
            lines += ["(no issues to display)", "", "---", ""]
            return lines

        lines += [
            "| # | Key | Summary | Priority | Days Blocked | Owner | Score |",
            "|---|---|---|---|---|---|---|",
        ]
        for rank, issue in enumerate(section.top_issues, start=1):
            lines.append(
                f"| {rank} | {_cell(issue.issue_key)} | {_cell(issue.summary)}"
                f" | {_cell(issue.priority or '-')} | {issue.days_blocked}"
                f" | {_cell(issue.owner)} | {issue.score:.0f} |"
            )
        lines.append("")

        for issue in section.top_issues:
            lines += _issue_detail_lines(issue)

        lines += ["---", ""]
        return lines

    def _issues_table(self, response: QueryResponse) -> list[str]:
        columns = response.display_fields or _default_columns(response.issues[0])
        log.debug("issues_table", columns=columns, first_issue=response.issues[0])
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


def _issue_value(issue: dict[str, Any], column: str) -> str:
    """Return the issue value for the given display column.

    Tries three forms of the backend-provided column name - no new names are invented:
      1. Exact string from display_fields     ("Issue Type", "Key", "Resolved")
      2. Lowercased                           ("issue type", "key", "resolved")
      3. Lowercased with spaces removed       ("issuetype",  "key", "resolved")
    A null value at an earlier candidate does not block a non-null value at a later one.
    The backend sends both "Key": null and "key": "PROJ-123" in the same issue dict;
    skipping nulls ensures the real value in the lowercase form is found.
    """
    lower = column.lower()
    for candidate in (column, lower, lower.replace(" ", "")):
        if candidate in issue:
            v = issue[candidate]
            if v is not None:
                return str(v)
    return ""


def _issue_detail_lines(issue: BlockerAnalysis) -> list[str]:
    deps = ", ".join(issue.dependent_issues) if issue.dependent_issues else "none"
    citations = (
        ", ".join(
            c.issue_key + (f" comment {c.comment_id}" if c.comment_id else "")
            for c in issue.evidence
        )
        if issue.evidence
        else "none"
    )
    return [
        f"### {issue.issue_key}: {_cell(issue.summary)}",
        "",
        f"**Blocked reason (FACT):** {_cell(issue.blocked_reason)}",
        f"**Dependent issues (FACT):** {deps}",
        f"**Citations:** {citations}",
        "",
        f"**Suggested resolution (AI SUGGESTION):** {_cell(issue.suggested_resolution)}",
        f"**Mitigation (AI SUGGESTION):** {_cell(issue.mitigation)}",
        f"**Risk (AI SUGGESTION):** {_cell(issue.risk_note)}",
        "",
    ]


def _cell(value: object) -> str:
    """One markdown table cell: single line, pipes escaped."""
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")
