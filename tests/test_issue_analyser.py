"""IssueAnalyser: LLM mocked via TestModel/FunctionModel (no live network or LLM)."""

import datetime
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from config.settings import AnalysisSettings
from core.issue_analyser import IssueAnalyser, _compute_days_blocked, build_issue_analyser
from models.lite import (
    ChangelogEntry,
    IssueComment,
    IssueDetail,
    IssueDetailsResponse,
    IssueLink,
)
from models.responses import IssueAnalysisSuggestions

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPT_PATH = REPO_ROOT / "prompts" / "issue_analysis_prompt.md"

_TODAY = datetime.date(2026, 6, 13)

_GOOD_SUGGESTIONS = {
    "suggested_resolution": "Escalate to the vendor and request a root-cause timeline.",
    "mitigation": "Pause dependent work until unblocked.",
    "risk_note": "CAR-205 sign-off will slip if this is not resolved by end of week.",
    "evidence": [{"issue_key": "CAR-101", "comment_id": "10042"}],
}


def _make_analyser(model: object, blocked_statuses: frozenset[str] | None = None) -> IssueAnalyser:
    agent: Agent[None, IssueAnalysisSuggestions] = Agent(  # type: ignore[call-overload]
        model,
        output_type=IssueAnalysisSuggestions,
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
    )
    return IssueAnalyser(agent=agent, blocked_statuses=blocked_statuses, today=_TODAY)


def _issue(
    key: str = "CAR-101",
    *,
    assignee: str | None = "jdoe",
    comments: list[IssueComment] | None = None,
    links: list[IssueLink] | None = None,
    changelog: list[ChangelogEntry] | None = None,
    priority: str | None = "Critical",
    due_date: str | None = None,
    flagged: bool = False,
) -> IssueDetail:
    return IssueDetail(
        key=key,
        summary=f"Issue {key}",
        priority=priority,
        assignee=assignee,
        due_date=due_date,
        flagged=flagged,
        comments=comments or [],
        links=links or [],
        changelog=changelog or [],
    )


def _response(*issues: IssueDetail) -> IssueDetailsResponse:
    return IssueDetailsResponse(issues=list(issues))


def _changelog_entry(to_value: str, days_ago: int) -> ChangelogEntry:
    ts = datetime.datetime(_TODAY.year, _TODAY.month, _TODAY.day) - datetime.timedelta(
        days=days_ago
    )
    return ChangelogEntry(
        field="status",
        from_value="In Progress",
        to_value=to_value,
        author="jdoe",
        timestamp=ts.isoformat() + ".000+0000",
    )


# ---------------------------------------------------------------------------
# days_blocked computation (pure Python, no LLM needed)
# ---------------------------------------------------------------------------


def test_days_blocked_from_changelog() -> None:
    issue = _issue(changelog=[_changelog_entry("Blocked", 5)])
    result = _compute_days_blocked(issue, frozenset({"blocked"}), _TODAY)
    assert result == 5


def test_days_blocked_uses_most_recent_blocked_entry() -> None:
    issue = _issue(
        changelog=[
            _changelog_entry("Blocked", 10),  # older
            _changelog_entry("In Progress", 7),
            _changelog_entry("Blocked", 3),  # more recent
        ]
    )
    result = _compute_days_blocked(issue, frozenset({"blocked"}), _TODAY)
    assert result == 3


def test_days_blocked_zero_when_issue_transitioned_out_of_blocked() -> None:
    # Issue was blocked but most recent transition is back to In Progress - not currently blocked.
    issue = _issue(
        changelog=[
            _changelog_entry("Blocked", 5),
            _changelog_entry("In Progress", 1),  # most recent
        ]
    )
    result = _compute_days_blocked(issue, frozenset({"blocked"}), _TODAY)
    assert result == 0


def test_days_blocked_zero_when_no_blocked_status() -> None:
    issue = _issue(changelog=[_changelog_entry("In Progress", 5)])
    result = _compute_days_blocked(issue, frozenset({"blocked"}), _TODAY)
    assert result == 0


def test_days_blocked_case_insensitive() -> None:
    issue = _issue(changelog=[_changelog_entry("BLOCKED", 4)])
    result = _compute_days_blocked(issue, frozenset({"blocked"}), _TODAY)
    assert result == 4


def test_days_blocked_zero_when_no_changelog() -> None:
    issue = _issue(changelog=[])
    result = _compute_days_blocked(issue, frozenset({"blocked"}), _TODAY)
    assert result == 0


# ---------------------------------------------------------------------------
# Full analyse() with mocked LLM
# ---------------------------------------------------------------------------


async def test_analyse_returns_blocker_analysis() -> None:
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    detail = _issue(
        comments=[
            IssueComment(
                id="10042",
                author="jdoe",
                body="Vendor not responding.",
                created="2026-06-10T08:00:00.000+0000",
                updated="2026-06-10T08:00:00.000+0000",
            )
        ],
        links=[IssueLink(type="blocks", direction="outward", linked_issue_key="CAR-205")],
        changelog=[_changelog_entry("Blocked", 5)],
    )
    results = await analyser.analyse(_response(detail), ["CAR-101"])
    assert len(results) == 1
    r = results[0]
    assert r.issue_key == "CAR-101"
    assert r.days_blocked == 5
    assert r.owner == "jdoe"
    assert r.dependent_issues == ["CAR-205"]
    assert r.suggested_resolution == _GOOD_SUGGESTIONS["suggested_resolution"]
    assert len(r.evidence) == 1
    assert r.evidence[0].comment_id == "10042"


async def test_analyse_concurrent_multiple_keys() -> None:
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    issues = [_issue(key=f"CAR-{i}") for i in range(3)]
    results = await analyser.analyse(_response(*issues), ["CAR-0", "CAR-1", "CAR-2"])
    assert len(results) == 3
    keys = {r.issue_key for r in results}
    assert keys == {"CAR-0", "CAR-1", "CAR-2"}


async def test_analyse_dependent_issues_from_links() -> None:
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    detail = _issue(
        links=[
            IssueLink(type="blocks", direction="outward", linked_issue_key="CAR-A"),
            IssueLink(type="relates to", direction="inward", linked_issue_key="CAR-B"),
        ]
    )
    results = await analyser.analyse(_response(detail), ["CAR-101"])
    assert results[0].dependent_issues == ["CAR-A", "CAR-B"]


async def test_analyse_missing_key_produces_degraded_result() -> None:
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    results = await analyser.analyse(_response(), ["CAR-999"])
    assert len(results) == 1
    assert results[0].issue_key == "CAR-999"
    assert results[0].days_blocked == 0
    assert results[0].suggested_resolution == "(unavailable)"


async def test_analyse_llm_failure_produces_degraded_result() -> None:
    def always_prose(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="I cannot help with this.")])

    analyser = _make_analyser(FunctionModel(always_prose))
    detail = _issue()
    results = await analyser.analyse(_response(detail), ["CAR-101"])
    assert len(results) == 1
    assert results[0].suggested_resolution == "(unavailable)"


async def test_analyse_cache_hit_skips_second_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = {"n": 0}

    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)

    # Track calls by monkeypatching _analyse_one
    original = analyser._analyse_one

    async def counting_analyse_one(issue: IssueDetail, summaries: dict[str, str] | None) -> object:
        call_count["n"] += 1
        return await original(issue, summaries)

    monkeypatch.setattr(analyser, "_analyse_one", counting_analyse_one)

    detail = _issue(changelog=[_changelog_entry("Blocked", 3)])
    detail_response = _response(detail)

    await analyser.analyse(detail_response, ["CAR-101"])
    await analyser.analyse(detail_response, ["CAR-101"])  # same key + same timestamp

    assert call_count["n"] == 1  # second call hits cache


async def test_analyse_unassigned_owner() -> None:
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    detail = _issue(assignee=None)
    results = await analyser.analyse(_response(detail), ["CAR-101"])
    assert results[0].owner == "Unassigned"


async def test_analyse_summary_from_summaries_dict() -> None:
    """When IssueDetail.summary is None, the summaries dict value is used."""
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    detail = IssueDetail(key="CAR-101", summary=None, comments=[], links=[], changelog=[])
    results = await analyser.analyse(
        _response(detail), ["CAR-101"], summaries={"CAR-101": "Engine stall on cold start"}
    )
    assert results[0].summary == "Engine stall on cold start"


async def test_analyse_summary_fallback_when_no_summary() -> None:
    """When both IssueDetail.summary and summaries dict are absent, fallback is used."""
    model = TestModel(custom_output_args=_GOOD_SUGGESTIONS)
    analyser = _make_analyser(model)
    detail = IssueDetail(key="CAR-101", summary=None, comments=[], links=[], changelog=[])
    results = await analyser.analyse(_response(detail), ["CAR-101"], summaries=None)
    assert results[0].summary == "(no summary)"


def test_build_issue_analyser_wires_settings() -> None:
    """Factory passes max_concurrency and blocked_statuses from AnalysisSettings."""
    settings = AnalysisSettings(
        max_concurrency=3,
        blocked_statuses=["On Hold", "Waiting"],
        prompt_path=PROMPT_PATH,
    )
    analyser = build_issue_analyser("test", settings)
    assert analyser._max_concurrency == 3
    assert analyser._blocked_statuses == frozenset({"on hold", "waiting"})
