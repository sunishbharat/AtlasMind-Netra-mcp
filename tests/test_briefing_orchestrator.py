"""BriefingOrchestrator: all seams faked, no live LLM or network."""

import json
from pathlib import Path
from typing import Any

from briefings.delivery import BaseDeliveryChannel
from config.settings import Settings
from confluence.models.reference import ConfluenceReference
from core.briefing_orchestrator import BriefingOrchestrator
from core.exceptions import DecompositionError, LiteBackendError
from core.report_synthesiser import ReportSynthesiser
from memory.briefing_session_store import InMemoryBriefingSessionStore
from models.lite import IssueDetailsResponse, LiteQueryResult
from models.responses import (
    AgendaTopic,
    BlockerAnalysis,
    BriefingSection,
    QueryResponse,
)

# ---------------------------------------------------------------------------
# Fake collaborators
# ---------------------------------------------------------------------------


class FakeDecomposer:
    def __init__(self, topics: list[AgendaTopic] | None = None, fail: bool = False) -> None:
        self.topics = topics or []
        self.fail = fail
        self.calls: list[tuple[str, list[str] | None]] = []

    async def decompose(
        self, agenda_text: str, projects: list[str] | None = None
    ) -> list[AgendaTopic]:
        self.calls.append((agenda_text, projects))
        if self.fail:
            raise DecompositionError("decomposer failed")
        return self.topics


class FakeQueryHandler:
    def __init__(
        self,
        results: dict[str, QueryResponse] | None = None,
        requires_input_for: set[str] | None = None,
    ) -> None:
        self._results = results or {}
        self._requires_input_for = requires_input_for or set()
        self.calls: list[dict[str, Any]] = []

    async def handle_query(
        self,
        *,
        query: str,
        session_id: str,
        clarification_answer: str | None = None,
        elicit: Any = None,
        limit: int | None = None,
        show_in_ui: bool = False,
    ) -> QueryResponse:
        self.calls.append({"query": query, "session_id": session_id, "ca": clarification_answer})
        key = session_id.split("__topic_")[-1] if "__topic_" in session_id else query
        if key in self._requires_input_for:
            return QueryResponse(
                session_id=session_id,
                requires_user_input=True,
                clarification_question=f"clarify {key}?",
            )
        if key in self._results:
            return self._results[key]
        return QueryResponse(
            session_id=session_id,
            jql=f"project = {key.upper()}",
            total=2,
            shown=2,
            issues=[{"key": "CAR-1", "summary": "bug"}],
        )


class FakeLiteClient:
    def __init__(self, details: IssueDetailsResponse | None = None, fail: bool = False) -> None:
        self._details = details or IssueDetailsResponse(issues=[])
        self.fail = fail
        self.calls: list[list[str]] = []

    async def query(self, text: str, **kwargs: Any) -> LiteQueryResult:
        return LiteQueryResult()

    async def get_issue_details(
        self,
        issue_keys: list[str],
        *,
        comments_limit: int | None = None,
        request_id: str | None = None,
    ) -> IssueDetailsResponse:
        self.calls.append(list(issue_keys))
        if self.fail:
            raise LiteBackendError("network error")
        return self._details


class FakeAnalyser:
    def __init__(self, analyses: list[BlockerAnalysis] | None = None) -> None:
        self._analyses = analyses or []

    async def analyse(
        self,
        issue_details: IssueDetailsResponse,
        issue_keys: list[str],
        summaries: dict[str, str] | None = None,
        confluence_refs: dict[str, list[ConfluenceReference]] | None = None,
    ) -> list[BlockerAnalysis]:
        return self._analyses


class FakeRanker:
    def rank(
        self, analyses: list[BlockerAnalysis], top_n: int | None = None
    ) -> list[BlockerAnalysis]:
        n = top_n or 5
        return analyses[:n]


class FakeDeliveryChannel(BaseDeliveryChannel):
    def __init__(self, path: str = "") -> None:
        self._path = path
        self.delivered: list[tuple[str, str]] = []

    async def deliver(self, *, report_id: str, content: str) -> str:
        self.delivered.append((report_id, content))
        return self._path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TOPICS = [
    AgendaTopic(
        topic_id="topic_1",
        description="Carline XX blockers",
        suggested_query="blockers in carline XX",
    ),
    AgendaTopic(
        topic_id="topic_2",
        description="BOM criticals",
        suggested_query="critical issues in BOM",
    ),
]

_ANALYSIS = BlockerAnalysis(
    issue_key="CAR-1",
    summary="critical bug",
    blocked_reason="vendor delay",
    days_blocked=5,
    owner="jdoe",
    suggested_resolution="escalate",
    mitigation="pause deps",
    risk_note="slips timeline",
)


def _make_orch(
    settings: Settings,
    decomposer: FakeDecomposer | None = None,
    query_handler: FakeQueryHandler | None = None,
    lite_client: FakeLiteClient | None = None,
    analyser: FakeAnalyser | None = None,
    delivery: FakeDeliveryChannel | None = None,
    tmp_path: Path | None = None,
) -> BriefingOrchestrator:
    if tmp_path is not None:
        settings.delivery.output_dir = tmp_path

    return BriefingOrchestrator(
        decomposer=decomposer or FakeDecomposer(topics=_TOPICS),
        query_handler=query_handler or FakeQueryHandler(),
        lite_client=lite_client or FakeLiteClient(),
        issue_analyser=analyser or FakeAnalyser(analyses=[_ANALYSIS]),
        ranking_engine=FakeRanker(),
        report_synthesiser=ReportSynthesiser(),
        delivery_channel=delivery
        or FakeDeliveryChannel(str(tmp_path / "r.md") if tmp_path else ""),
        briefing_sessions=InMemoryBriefingSessionStore(ttl_seconds=600),
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_happy_path_returns_briefing_with_sections(
    settings: Settings, tmp_path: Path
) -> None:
    delivery = FakeDeliveryChannel(str(tmp_path / "report.md"))
    orch = _make_orch(
        settings,
        analyser=FakeAnalyser(analyses=[_ANALYSIS]),
        delivery=delivery,
        tmp_path=tmp_path,
    )
    response = await orch.generate_briefing(agenda_text="daily", session_id="s1")

    assert not response.requires_user_input
    assert len(response.sections) == 2
    assert response.sections[0].topic_id == "topic_1"
    assert response.sections[0].top_issues == [_ANALYSIS]
    assert len(delivery.delivered) == 1


async def test_decomposition_failure_returns_error_response(settings: Settings) -> None:
    orch = _make_orch(settings, decomposer=FakeDecomposer(fail=True))
    response = await orch.generate_briefing(agenda_text="daily", session_id="s1")

    assert not response.requires_user_input
    assert any("decomposition failed" in e for e in response.errors)


async def test_empty_topics_returns_error_response(settings: Settings) -> None:
    orch = _make_orch(settings, decomposer=FakeDecomposer(topics=[]))
    response = await orch.generate_briefing(agenda_text="daily", session_id="s1")

    assert any("no data questions" in e for e in response.errors)


async def test_topic_needing_clarification_returns_question(settings: Settings) -> None:
    qh = FakeQueryHandler(requires_input_for={"topic_1"})
    orch = _make_orch(settings, query_handler=qh)
    response = await orch.generate_briefing(agenda_text="daily", session_id="s2")

    assert response.requires_user_input
    assert "topic_1" in (response.pending_topic_id or "")
    assert response.clarification_question is not None


async def test_clarification_answer_resumes_from_saved_state(
    settings: Settings, tmp_path: Path
) -> None:
    settings.delivery.output_dir = tmp_path
    # First call: topic_1 needs clarification
    qh = FakeQueryHandler(requires_input_for={"topic_1"})
    store = InMemoryBriefingSessionStore(ttl_seconds=600)
    orch = BriefingOrchestrator(
        decomposer=FakeDecomposer(topics=_TOPICS),
        query_handler=qh,
        lite_client=FakeLiteClient(),
        issue_analyser=FakeAnalyser(analyses=[_ANALYSIS]),
        ranking_engine=FakeRanker(),
        report_synthesiser=ReportSynthesiser(),
        delivery_channel=FakeDeliveryChannel(str(tmp_path / "report.md")),
        briefing_sessions=store,
        settings=settings,
    )
    r1 = await orch.generate_briefing(agenda_text="daily", session_id="s3")
    assert r1.requires_user_input

    # Second call: remove topic_1 from requires_input set and provide answer
    qh._requires_input_for = set()
    r2 = await orch.generate_briefing(
        agenda_text="daily", session_id="s3", clarification_answer="label=escalation"
    )
    assert not r2.requires_user_input
    assert len(r2.sections) == 2
    assert r2.errors == []


async def test_issue_details_failure_degrades_gracefully(
    settings: Settings, tmp_path: Path
) -> None:
    delivery = FakeDeliveryChannel(str(tmp_path / "r.md"))
    orch = _make_orch(
        settings,
        lite_client=FakeLiteClient(fail=True),
        delivery=delivery,
        tmp_path=tmp_path,
    )
    response = await orch.generate_briefing(agenda_text="daily", session_id="s4")

    assert not response.requires_user_input
    assert any("issue_details fetch failed" in e for e in response.errors)
    assert len(response.sections) == 2
    assert len(delivery.delivered) == 1


async def test_delivery_disabled_no_report_path(settings: Settings) -> None:
    settings.delivery.enabled = False
    orch = _make_orch(settings)
    response = await orch.generate_briefing(agenda_text="daily", session_id="s5")

    assert response.report_path is None


async def test_get_briefing_report_missing_returns_error(
    settings: Settings, tmp_path: Path
) -> None:
    settings.delivery.output_dir = tmp_path
    orch = _make_orch(settings, tmp_path=tmp_path)
    result = await orch.get_briefing_report("nonexistent_report", session_id="s_missing")

    assert result.report_id == "nonexistent_report"
    assert result.errors


async def test_view_url_set_when_base_configured(settings: Settings, tmp_path: Path) -> None:
    settings.briefing.view_url_base = "http://frontend/briefing"
    delivery = FakeDeliveryChannel(str(tmp_path / "r.md"))
    orch = _make_orch(settings, delivery=delivery, tmp_path=tmp_path)
    response = await orch.generate_briefing(agenda_text="daily", session_id="s6")

    assert response.view_url is not None
    assert response.view_url.startswith("http://frontend/briefing/")


async def test_build_sections_respects_max_analysed_issues_cap(
    settings: Settings, tmp_path: Path
) -> None:
    # 3 topics, 1 unique issue each -> 3 unique keys before the cap
    topics = [
        AgendaTopic(topic_id=f"topic_{i}", description=f"t{i}", suggested_query=f"q{i}")
        for i in range(1, 4)
    ]
    qh = FakeQueryHandler(
        results={
            f"topic_{i}": QueryResponse(
                session_id=f"s_cap_{i}",
                issues=[{"key": f"CAR-{i}", "summary": f"issue {i}"}],
                total=1,
                shown=1,
            )
            for i in range(1, 4)
        }
    )
    lite = FakeLiteClient()
    settings.briefing.max_analysed_issues = 2

    orch = _make_orch(
        settings,
        decomposer=FakeDecomposer(topics=topics),
        query_handler=qh,
        lite_client=lite,
        tmp_path=tmp_path,
    )
    response = await orch.generate_briefing(agenda_text="three topics", session_id="cap_test")

    # Only 2 of the 3 unique keys should reach get_issue_details
    assert lite.calls, "get_issue_details was not called"
    assert len(lite.calls[0]) == 2
    assert lite.calls[0] == ["CAR-1", "CAR-2"]

    # topic_3's issues were dropped; its section must carry a truncation error
    topic_3_section = next(s for s in response.sections if s.topic_id == "topic_3")
    assert any("dropped" in e and "max_analysed_issues" in e for e in topic_3_section.errors), (
        f"topic_3 should have truncation error, got: {topic_3_section.errors}"
    )


async def test_get_briefing_report_with_valid_json_returns_sections(
    settings: Settings, tmp_path: Path
) -> None:
    """get_briefing_report reads the JSON sidecar and returns sections without errors."""
    settings.delivery.output_dir = tmp_path
    orch = _make_orch(settings, tmp_path=tmp_path)

    report_id = "happy_path_report"
    section = BriefingSection(
        topic_id="topic_1",
        description="Blockers",
        query_used="blockers",
        jql="project = CAR",
        top_issues=[],
        total_found=5,
        errors=[],
    )
    sidecar = {"report_id": report_id, "sections": [section.model_dump()]}
    (tmp_path / f"{report_id}.json").write_text(json.dumps(sidecar), encoding="utf-8")

    result = await orch.get_briefing_report(report_id, session_id="s_valid")

    assert result.report_id == report_id
    assert len(result.sections) == 1
    assert result.sections[0].topic_id == "topic_1"
    assert result.errors == []
