"""Per-issue content analysis (design doc: IssueAnalyser).

Turns raw issue data from POST /issue_details into a BlockerAnalysis per issue.
Fact fields (days_blocked, owner, dependent_issues) are computed in pure Python before
the LLM call; the LLM fills only the three AI SUGGESTION fields. This enforces the
trust rules: LLM explains, never calculates (design doc: Trust rules).

Analyses run concurrently (asyncio.gather + bounded semaphore). Results are cached by
(issue_key, last_changelog_timestamp) so unchanged issues skip re-analysis on the next
run.
"""

import asyncio
import datetime
from typing import Protocol

import structlog
from pydantic_ai import Agent
from pydantic_ai.exceptions import AgentRunError, UserError
from pydantic_ai.models import Model

from config.settings import AnalysisSettings
from core.exceptions import AnalysisError, ConfigError
from models.lite import IssueComment, IssueDetail, IssueDetailsResponse
from models.responses import BlockerAnalysis, IssueAnalysisSuggestions

logger = structlog.get_logger(__name__)

_DEFAULT_BLOCKED_STATUSES = frozenset({"blocked", "stalled", "on hold", "waiting"})


class IssueAnalyserPort(Protocol):
    """Analysis seam for the briefing pipeline."""

    async def analyse(
        self,
        issue_details: IssueDetailsResponse,
        issue_keys: list[str],
        summaries: dict[str, str] | None = None,
    ) -> list[BlockerAnalysis]: ...


class IssueAnalyser:
    """Runs per-issue LLM analysis and returns a BlockerAnalysis for each key.

    The PydanticAI agent is injected already-constructed so tests can pass
    TestModel/FunctionModel without a live LLM (same pattern as Clarifier).

    `today` is injectable for deterministic testing of days_blocked computation.
    """

    def __init__(
        self,
        agent: Agent[None, IssueAnalysisSuggestions],
        *,
        max_concurrency: int = 5,
        blocked_statuses: frozenset[str] | None = None,
        today: datetime.date | None = None,
        cache_max_size: int = 1000,
    ) -> None:
        self._agent = agent
        self._max_concurrency = max_concurrency
        self._blocked_statuses = blocked_statuses or _DEFAULT_BLOCKED_STATUSES
        self._today = today
        self._cache: dict[tuple[str, str], BlockerAnalysis] = {}
        self._cache_max_size = cache_max_size
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def analyse(
        self,
        issue_details: IssueDetailsResponse,
        issue_keys: list[str],
        summaries: dict[str, str] | None = None,
    ) -> list[BlockerAnalysis]:
        """Analyse each key from the pre-fetched IssueDetailsResponse.

        `summaries` maps issue_key -> summary text from POST /query results; used because
        POST /issue_details does not include a summary field. Falls back to "(no summary)"
        when both the model field and the dict are absent.

        Keys not present in `issue_details.issues` produce a degraded BlockerAnalysis
        (days_blocked=0, empty AI fields) - partial failure is a designed state.
        """
        issue_map = {d.key: d for d in issue_details.issues}

        async def bounded(key: str) -> BlockerAnalysis:
            async with self._semaphore:
                return await self._analyse_key(key, issue_map, summaries)

        tasks = [bounded(key) for key in issue_keys]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: list[BlockerAnalysis] = []
        for key, result in zip(issue_keys, raw_results, strict=False):
            if isinstance(result, BaseException):
                logger.warning(
                    "issue_analysis_failed",
                    issue_key=key,
                    error=str(result),
                )
                results.append(_degraded(key, f"analysis failed: {result}"))
            else:
                results.append(result)
        return results

    async def _analyse_key(
        self,
        key: str,
        issue_map: dict[str, IssueDetail],
        summaries: dict[str, str] | None,
    ) -> BlockerAnalysis:
        issue = issue_map.get(key)
        if issue is None:
            logger.warning("issue_detail_missing", issue_key=key)
            return _degraded(key, "issue not found in /issue_details response")

        cache_ts = issue.changelog[-1].timestamp if issue.changelog else ""
        cache_key = (key, cache_ts)
        if cache_key in self._cache:
            return self._cache[cache_key]

        result = await self._analyse_one(issue, summaries)
        if self._cache_max_size > 0:
            if len(self._cache) >= self._cache_max_size:
                self._cache.pop(next(iter(self._cache)))
            self._cache[cache_key] = result
        return result

    async def _analyse_one(
        self, issue: IssueDetail, summaries: dict[str, str] | None
    ) -> BlockerAnalysis:
        today = self._today or datetime.date.today()

        owner = issue.assignee or "Unassigned"
        dependent_issues = [link.linked_issue_key for link in issue.links]
        days_blocked = _compute_days_blocked(issue, self._blocked_statuses, today)
        summary = issue.summary or (summaries or {}).get(issue.key) or "(no summary)"

        # Sort comments newest-first (Jira default is ascending/oldest-first).
        sorted_comments = sorted(issue.comments, key=lambda c: c.created, reverse=True)

        prompt = _build_prompt(issue, summary, sorted_comments)
        try:
            run_result = await self._agent.run(prompt)
            suggestions: IssueAnalysisSuggestions = run_result.output
        except (AgentRunError, UserError) as exc:
            raise AnalysisError(f"analysis LLM call failed for {issue.key}: {exc}") from exc

        # blocked_reason: synthesise from comments when available, otherwise note absence.
        if sorted_comments:
            latest = sorted_comments[0]
            blocked_reason = f"Latest comment ({latest.author}): {latest.body[:200]}"
        else:
            blocked_reason = "No comments available - blocked reason unclear."

        return BlockerAnalysis(
            issue_key=issue.key,
            summary=summary,
            blocked_reason=blocked_reason,
            days_blocked=days_blocked,
            owner=owner,
            priority=issue.priority,
            dependent_issues=dependent_issues,
            due_date=issue.due_date,
            flagged=issue.flagged,
            suggested_resolution=suggestions.suggested_resolution,
            mitigation=suggestions.mitigation,
            risk_note=suggestions.risk_note,
            evidence=suggestions.evidence,
        )


def build_issue_analyser(
    model: Model | str,
    settings: AnalysisSettings,
    retries: int = 2,
) -> IssueAnalyser:
    """Factory used by server.py to construct IssueAnalyser from AnalysisSettings."""
    try:
        system_prompt = settings.prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"{settings.prompt_path}: cannot read issue analysis prompt: {exc}"
        ) from exc
    agent: Agent[None, IssueAnalysisSuggestions] = Agent(
        model, output_type=IssueAnalysisSuggestions, system_prompt=system_prompt, retries=retries
    )
    return IssueAnalyser(
        agent=agent,
        max_concurrency=settings.max_concurrency,
        blocked_statuses=frozenset(s.strip().lower() for s in settings.blocked_statuses),
        cache_max_size=settings.analysis_cache_max_size,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_days_blocked(
    issue: IssueDetail,
    blocked_statuses: frozenset[str],
    today: datetime.date,
) -> int:
    """Return days the issue has been continuously blocked, or 0 if currently unblocked.

    The most recent changelog entry determines current status. If it is not a blocked
    status, the issue is considered unblocked regardless of its history.
    """
    # Changelog is ascending; the first entry in reverse is the current (most recent) status.
    for entry in reversed(issue.changelog):
        to_val = entry.to_value.strip().lower()
        if to_val not in blocked_statuses:
            return 0  # most recent transition was out of blocked
        try:
            entry_date = datetime.datetime.fromisoformat(entry.timestamp).date()
            return max(0, (today - entry_date).days)
        except ValueError:
            logger.warning(
                "changelog_invalid_timestamp",
                issue_key=issue.key,
                timestamp=entry.timestamp,
            )
            return 0
    return 0


def _build_prompt(issue: IssueDetail, summary: str, sorted_comments: list[IssueComment]) -> str:
    lines = [
        "OPERATION: ANALYSE ISSUE",
        f"ISSUE KEY: {issue.key}",
        f"SUMMARY: {summary}",
        f"PRIORITY: {issue.priority or 'unknown'}",
        f"ASSIGNEE: {issue.assignee or 'unassigned'}",
        f"DUE DATE: {issue.due_date or 'not set'}",
        f"FLAGGED: {issue.flagged}",
    ]

    if issue.links:
        link_lines = [
            f"  - {link.type} ({link.direction}): {link.linked_issue_key}"
            + (f" - {link.linked_issue_summary}" if link.linked_issue_summary else "")
            for link in issue.links
        ]
        lines += ["ISSUE LINKS:", *link_lines]
    else:
        lines.append("ISSUE LINKS: none")

    if sorted_comments:
        lines.append("COMMENTS (newest first, with comment IDs for citation):")
        for comment in sorted_comments:
            lines.append(
                f"  [id:{comment.id}] {comment.author} ({comment.created[:10]}): {comment.body}"
            )
    else:
        lines.append("COMMENTS: none")

    lines += [
        "",
        "-> Fill suggested_resolution, mitigation, and risk_note (2-3 sentences each).",
        "-> In evidence, cite only comment IDs listed above that directly support your analysis.",
        "-> Never invent facts. Work only from the provided issue data.",
    ]
    return "\n".join(lines)


def _degraded(key: str, reason: str) -> BlockerAnalysis:
    """Minimal BlockerAnalysis returned when analysis fails (partial failure design)."""
    return BlockerAnalysis(
        issue_key=key,
        summary="(analysis unavailable)",
        blocked_reason=reason,
        days_blocked=0,
        owner="unknown",
        priority=None,
        suggested_resolution="(unavailable)",
        mitigation="(unavailable)",
        risk_note="(unavailable)",
    )
