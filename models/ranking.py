"""RankingRule config models (design doc: Ranking engine).

These models validate config/ranking_default.json at startup. They are kept in models/ so
they are importable without pulling in any core/ dependencies.
"""

from pydantic import BaseModel, ConfigDict, Field


class PriorityWeights(BaseModel):
    """Score added per priority level. Unknown priorities get `default`."""

    model_config = ConfigDict(frozen=True)

    Blocker: int = 40
    Critical: int = 30
    High: int = 15
    default: int = 0


class DaysBlockedWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_day: int = 2
    cap: int = 30


class DependentIssuesWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    per_issue: int = 5
    cap: int = 25


class MilestoneProximityWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    due_within_7d: int = 20
    due_within_14d: int = 10


class RankingWeights(BaseModel):
    model_config = ConfigDict(frozen=True)

    priority: PriorityWeights = Field(default_factory=PriorityWeights)
    days_blocked: DaysBlockedWeights = Field(default_factory=DaysBlockedWeights)
    dependent_issues: DependentIssuesWeights = Field(default_factory=DependentIssuesWeights)
    milestone_proximity: MilestoneProximityWeights = Field(
        default_factory=MilestoneProximityWeights
    )
    flagged: int = 10


class RankingRule(BaseModel):
    """Complete ranking configuration loaded from config/ranking_default.json."""

    model_config = ConfigDict(frozen=True)

    weights: RankingWeights = Field(default_factory=RankingWeights)
    top_n: int = 5
