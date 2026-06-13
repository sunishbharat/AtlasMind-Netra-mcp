"""RankingEngine: deterministic scoring (no LLM, no mocking needed)."""

import datetime
from pathlib import Path

import pytest

from core.exceptions import ConfigError
from core.ranking_engine import RankingEngine, load_ranking_rule
from models.ranking import RankingRule
from models.responses import BlockerAnalysis

_TODAY = datetime.date(2026, 6, 13)

_BASE_ROOT = Path(__file__).parent.parent


def _analysis(
    *,
    key: str = "CAR-1",
    priority: str | None = None,
    days_blocked: int = 0,
    dependent_issues: list[str] | None = None,
    due_date: str | None = None,
    flagged: bool = False,
) -> BlockerAnalysis:
    return BlockerAnalysis(
        issue_key=key,
        summary="test issue",
        blocked_reason="test",
        days_blocked=days_blocked,
        owner="jdoe",
        dependent_issues=dependent_issues or [],
        due_date=due_date,
        flagged=flagged,
        priority=priority,
        suggested_resolution="fix it",
        mitigation="work around it",
        risk_note="things break",
    )


def _engine(rule: RankingRule | None = None) -> RankingEngine:
    return RankingEngine(rule or RankingRule(), today=_TODAY)


def test_priority_blocker() -> None:
    result = _engine().rank([_analysis(priority="Blocker")], top_n=1)
    assert result[0].score == 40.0


def test_priority_critical() -> None:
    result = _engine().rank([_analysis(priority="Critical")], top_n=1)
    assert result[0].score == 30.0


def test_priority_high() -> None:
    result = _engine().rank([_analysis(priority="High")], top_n=1)
    assert result[0].score == 15.0


def test_priority_unknown_uses_default() -> None:
    result = _engine().rank([_analysis(priority="Minor")], top_n=1)
    assert result[0].score == 0.0


def test_days_blocked_score() -> None:
    result = _engine().rank([_analysis(days_blocked=5)], top_n=1)
    assert result[0].score == 10.0  # 5 * 2


def test_days_blocked_cap() -> None:
    result = _engine().rank([_analysis(days_blocked=20)], top_n=1)
    assert result[0].score == 30.0  # 20 * 2 = 40, capped at 30


def test_dependent_issues_score() -> None:
    result = _engine().rank([_analysis(dependent_issues=["A", "B", "C"])], top_n=1)
    assert result[0].score == 15.0  # 3 * 5


def test_dependent_issues_cap() -> None:
    result = _engine().rank([_analysis(dependent_issues=["A", "B", "C", "D", "E", "F"])], top_n=1)
    assert result[0].score == 25.0  # 6 * 5 = 30, capped at 25


def test_milestone_proximity_within_7d() -> None:
    due = (_TODAY + datetime.timedelta(days=3)).isoformat()
    result = _engine().rank([_analysis(due_date=due)], top_n=1)
    assert result[0].score == 20.0


def test_milestone_proximity_within_14d() -> None:
    due = (_TODAY + datetime.timedelta(days=10)).isoformat()
    result = _engine().rank([_analysis(due_date=due)], top_n=1)
    assert result[0].score == 10.0


def test_milestone_proximity_beyond_14d() -> None:
    due = (_TODAY + datetime.timedelta(days=30)).isoformat()
    result = _engine().rank([_analysis(due_date=due)], top_n=1)
    assert result[0].score == 0.0


def test_milestone_proximity_overdue_gets_no_bonus() -> None:
    due = (_TODAY - datetime.timedelta(days=30)).isoformat()
    result = _engine().rank([_analysis(due_date=due)], top_n=1)
    assert result[0].score == 0.0


def test_flagged_bonus() -> None:
    result = _engine().rank([_analysis(flagged=True)], top_n=1)
    assert result[0].score == 10.0


def test_combined_score() -> None:
    # Blocker(40) + days_blocked=5(10) + 2 deps(10) + flagged(10) = 70
    a = _analysis(priority="Blocker", days_blocked=5, dependent_issues=["A", "B"], flagged=True)
    result = _engine().rank([a], top_n=1)
    assert result[0].score == 70.0


def test_top_n_slice() -> None:
    analyses = [_analysis(key=f"CAR-{i}", priority="Blocker") for i in range(5)]
    result = _engine().rank(analyses, top_n=3)
    assert len(result) == 3


def test_sort_descending() -> None:
    low = _analysis(key="LOW", priority="High")  # 15
    mid = _analysis(key="MID", priority="Critical")  # 30
    high = _analysis(key="HIGH", priority="Blocker")  # 40
    result = _engine().rank([low, mid, high], top_n=3)
    assert [a.issue_key for a in result] == ["HIGH", "MID", "LOW"]


def test_tie_breaking_preserves_input_order() -> None:
    a1 = _analysis(key="CAR-1", priority="Critical")
    a2 = _analysis(key="CAR-2", priority="Critical")
    a3 = _analysis(key="CAR-3", priority="Critical")
    result = _engine().rank([a1, a2, a3], top_n=3)
    assert [a.issue_key for a in result] == ["CAR-1", "CAR-2", "CAR-3"]


def test_model_copy_not_mutation() -> None:
    original = _analysis(priority="Blocker")
    assert original.score == 0.0  # unchanged
    result = _engine().rank([original], top_n=1)
    assert result[0].score == 40.0
    assert original.score == 0.0  # frozen model, never mutated


def test_load_ranking_rule_from_json() -> None:
    rule = load_ranking_rule(_BASE_ROOT / "config" / "ranking_default.json")
    assert rule.top_n == 5
    assert rule.weights.priority.Blocker == 40
    assert rule.weights.days_blocked.cap == 30


def test_load_ranking_rule_missing_file_raises() -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_ranking_rule(Path("/nonexistent/ranking.json"))


def test_invalid_due_date_skips_proximity_score() -> None:
    a = _analysis(due_date="not-a-date")
    result = _engine().rank([a], top_n=1)
    assert result[0].score == 0.0
