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
        "display_fields": ["key", "summary", "status"],
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


def test_key_null_twin_is_skipped() -> None:
    """Backend sends "Key": null alongside "key": value; the null must be skipped.

    Production data shape: display_fields has "Key"; issue dict has
    "key": "PROJ-1" (real value) and "Key": null (backend adds title-case form as null).
    The null "Key" (candidate 1) must not prevent "key" (candidate 2) from returning.
    """
    response = make_response(
        display_fields=["Key", "Summary"],
        issues=[{"key": "PROJ-1", "Key": None, "summary": "Some issue", "status": "Open"}],
        total=1,
        shown=1,
    )
    report = ReportSynthesiser().build_query_report(query="q", response=response)
    assert "| PROJ-1 |" in report


def test_real_backend_column_shapes() -> None:
    """Reflect actual backend response shape from production (ZooKeeper blockers query).

    Confirmed field names from live debug log (2026-06-21):
      - "key": "ZOOKEEPER-4923"             (lowercase, real value)
      - "Key": null                          (title-case, explicitly null - backend adds it)
      - "issuetype": "New Feature"           (no space; display_fields says "Issue Type")
      - "resolutiondate": "2025-12-15..."    (Jira field; display_fields says "Resolved")

    Note: "Resolved" column stays empty because "resolutiondate" cannot be derived from
    "Resolved" by case/space normalization. That is a backend data contract gap.
    """
    issue = {
        "key": "ZOOKEEPER-4923",
        "Key": None,
        "summary": "Support individual timeout to establish a brand-new session",
        "assignee": "Kezhu Wang",
        "status": "Resolved",
        "Status": "Resolved",
        "priority": "Blocker",
        "Priority": "Blocker",
        "issuetype": "New Feature",
        "created": "2025-04-25T16:23:27.000+0000",
        "resolutiondate": "2025-12-15T22:39:44.000+0000",
        "Project": "ZooKeeper",
    }
    response = make_response(
        display_fields=["Key", "Summary", "Assignee", "Status", "Issue Type", "Resolved"],
        issues=[issue],
        total=1,
        shown=1,
    )
    report = ReportSynthesiser().build_query_report(query="q", response=response)
    assert "| ZOOKEEPER-4923 |" in report    # Key: null "Key" skipped, lowercase "key" used
    assert "| New Feature |" in report        # Issue Type: space-stripped -> issuetype
