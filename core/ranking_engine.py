"""Deterministic issue ranking (design doc: RankingEngine).

Scoring is pure Python - no LLM involved. The LLM explains; it never calculates.
The rule weights are loaded from config/ranking_default.json and validated at startup.
"""

import datetime
import json
from pathlib import Path
from typing import Protocol

import structlog

from core.exceptions import ConfigError
from models.ranking import RankingRule
from models.responses import BlockerAnalysis

logger = structlog.get_logger(__name__)


class RankingEnginePort(Protocol):
    """Scoring seam for the briefing pipeline (Milestone 3)."""

    def rank(
        self, analyses: list[BlockerAnalysis], top_n: int | None = None
    ) -> list[BlockerAnalysis]: ...


def load_ranking_rule(path: Path) -> RankingRule:
    """Read and validate the ranking weights JSON file, raising ConfigError on failure."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read ranking rule: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: invalid JSON in ranking rule: {exc}") from exc
    try:
        return RankingRule.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"{path}: ranking rule does not match schema: {exc}") from exc


class RankingEngine:
    """Scores and sorts BlockerAnalysis items using a configurable RankingRule.

    All scoring is deterministic and reproducible - the Tuesday report is comparable to
    the Monday report (design doc: Ranking engine).

    `today` is injectable for testing; defaults to datetime.date.today() at call time.
    """

    def __init__(self, rule: RankingRule, today: datetime.date | None = None) -> None:
        self._rule = rule
        self._today = today

    def rank(
        self, analyses: list[BlockerAnalysis], top_n: int | None = None
    ) -> list[BlockerAnalysis]:
        """Score every analysis and return the top-N highest-scoring items.

        Returns new BlockerAnalysis instances (frozen model - scores are set via
        model_copy). Input order is preserved for equal scores (stable sort).
        """
        n = top_n if top_n is not None else self._rule.top_n
        today = self._today or datetime.date.today()
        scored = [self._score(a, today) for a in analyses]
        return sorted(scored, key=lambda a: a.score, reverse=True)[:n]

    def _score(self, analysis: BlockerAnalysis, today: datetime.date) -> BlockerAnalysis:
        w = self._rule.weights
        total: float = 0.0

        # Priority score
        priority_map = w.priority.model_dump()
        total += priority_map.get(analysis.priority or "", priority_map["default"])

        # Days-blocked score (capped)
        total += min(analysis.days_blocked * w.days_blocked.per_day, w.days_blocked.cap)

        # Dependent-issues score (capped)
        total += min(
            len(analysis.dependent_issues) * w.dependent_issues.per_issue,
            w.dependent_issues.cap,
        )

        # Milestone proximity score (overdue issues do not receive a proximity bonus)
        if analysis.due_date:
            try:
                due = datetime.date.fromisoformat(analysis.due_date)
                days_until_due = (due - today).days
                if days_until_due >= 0:
                    if days_until_due <= 7:
                        total += w.milestone_proximity.due_within_7d
                    elif days_until_due <= 14:
                        total += w.milestone_proximity.due_within_14d
            except ValueError:
                logger.warning(
                    "ranking_invalid_due_date",
                    issue_key=analysis.issue_key,
                    due_date=analysis.due_date,
                )

        # Flagged score
        if analysis.flagged:
            total += w.flagged

        return analysis.model_copy(update={"score": total})
