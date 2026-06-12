"""ReportSynthesiser: markdown structure, trust rules, table hygiene."""

from core.report_synthesiser import ReportSynthesiser
from models.lite import ChartSpec
from models.responses import AppliedConvention, QueryResponse


def make_response(**overrides: object) -> QueryResponse:
    base: dict[str, object] = {
        "session_id": "s1",
        "jql": "project = CAR AND labels = escalation",
        "answer": "Found 2 result(s).",
        "total": 2,
        "shown": 2,
        "display_fields": ["Key", "Summary", "Status"],
        "issues": [
            {"key": "CAR-1", "summary": "Brake ECU blocked", "status": "Blocked"},
            {"key": "CAR-2", "summary": "Firmware | v2 regression", "status": "Open"},
        ],
        "applied_conventions": [
            AppliedConvention(
                term="escalation", jql_hint="labels = escalation", source="convention"
            )
        ],
    }
    base.update(overrides)
    return QueryResponse.model_validate(base)


def test_report_contains_all_sections() -> None:
    report = ReportSynthesiser().build_query_report(
        query="show escalations", response=make_response()
    )
    assert "# AtlasMind query report" in report
    assert "> show escalations" in report
    assert "## Applied interpretations" in report
    assert "| escalation | `labels = escalation` | convention |" in report
    assert "```jql\nproject = CAR AND labels = escalation\n```" in report
    assert "## Issues (2 shown of 2 total)" in report
    assert "| CAR-1 | Brake ECU blocked | Blocked |" in report


def test_pipes_in_values_are_escaped() -> None:
    report = ReportSynthesiser().build_query_report(query="q", response=make_response())
    assert "Firmware \\| v2 regression" in report


def test_issue_rows_are_capped() -> None:
    issues = [{"key": f"CAR-{i}", "summary": "x", "status": "Open"} for i in range(5)]
    response = make_response(issues=issues, total=5, shown=5)
    report = ReportSynthesiser(max_issues=2).build_query_report(query="q", response=response)
    assert "| CAR-1 |" in report
    assert "| CAR-4 |" not in report
    assert "(3 more rows omitted)" in report


def test_chart_section_reflects_ui_state() -> None:
    spec = ChartSpec(type="bar", x_field="Assignee", y_field="Count", title="Bugs")
    report = ReportSynthesiser().build_query_report(
        query="q", response=make_response(chart_spec=spec, ui_injected=True)
    )
    assert "## Chart specification" in report
    assert "- type: bar" in report
    assert "- rendered in browser UI: yes" in report


def test_empty_result_and_warnings() -> None:
    response = make_response(
        issues=[], total=0, shown=0, errors=["backend down"], jql=None, answer=None
    )
    report = ReportSynthesiser().build_query_report(query="q", response=response)
    assert "(no issues returned)" in report
    assert "## Warnings" in report
    assert "- backend down" in report
    assert "```jql" not in report


def test_default_columns_when_display_fields_missing() -> None:
    response = make_response(display_fields=[])
    report = ReportSynthesiser().build_query_report(query="q", response=response)
    assert "| key | summary | status |" in report
