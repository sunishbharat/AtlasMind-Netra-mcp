"""Orchestrator: full clarification state machine with faked LLM and backend seams."""

from pathlib import Path

from briefings.delivery import BaseDeliveryChannel, build_delivery_channel
from config.settings import Settings
from core.exceptions import ClarificationError, LiteBackendError
from core.intent_classifier import IntentClassifier
from core.jira_fields_loader import JiraFieldsLoader
from core.orchestrator import Orchestrator
from core.report_synthesiser import ReportSynthesiser
from core.vocab_lookup import VocabLookup
from memory.conventions_store import Convention, JsonFileConventionsStore
from memory.session_store import ClarificationState, InMemorySessionStore
from models.clarification import ClarificationNeeded, ResolvedTerms, TermResolutionOutput
from models.frontend import InjectAck
from models.jira import JiraField
from models.lite import LiteQueryResult
from models.vocab import VocabEntry

QUESTION = "Does your team use label=escalation or priority=Critical/Blocker?"


class FakeClarifier:
    """Implements ClarifierPort deterministically and counts calls."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.formulate_calls = 0
        self.resolve_calls = 0

    async def formulate_question(
        self,
        *,
        query: str,
        terms: list[str],
        vocab: dict[str, VocabEntry],
        conventions: list[Convention],
        fields: list[JiraField],
    ) -> ClarificationNeeded:
        self.formulate_calls += 1
        if self.fail:
            raise ClarificationError("llm down")
        return ClarificationNeeded(question=QUESTION, terms=terms)

    async def resolve_answer(
        self,
        *,
        query: str,
        terms: list[str],
        answer: str,
        vocab: dict[str, VocabEntry],
    ) -> ResolvedTerms:
        self.resolve_calls += 1
        return ResolvedTerms(
            resolutions=[
                TermResolutionOutput(term=term, resolution_key="label", jql_hint=f"labels = {term}")
                for term in terms
            ]
        )


class FakeLite:
    """Implements LiteClientPort; records dispatched query texts."""

    def __init__(self, fail: bool = False, jql: str | None = "project = CAR") -> None:
        self.fail = fail
        self.jql = jql
        self.queries: list[str] = []

    async def query(
        self,
        text: str,
        *,
        limit: int | None = None,
        jira_token: str | None = None,
        jira_email: str | None = None,
        jira_url: str | None = None,
    ) -> LiteQueryResult:
        self.queries.append(text)
        if self.fail:
            raise LiteBackendError("backend down")
        return LiteQueryResult(
            type="jql", answer="Found 1 result(s).", jql=self.jql, total=1, shown=1
        )


class FakeFrontend:
    """Implements FrontendPort; records injected queries."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.injected: list[str] = []

    async def inject(self, query: str, request_id: str | None = None) -> InjectAck:
        self.injected.append(query)
        if self.accept:
            return InjectAck(accepted=True, request_id=request_id or "fake-id")
        return InjectAck(accepted=False, detail="No active UI session.")


def make_orchestrator(
    settings: Settings,
    clarifier: FakeClarifier | None = None,
    lite: FakeLite | None = None,
    frontend: FakeFrontend | None = None,
    delivery: BaseDeliveryChannel | None = None,
) -> tuple[Orchestrator, FakeClarifier, FakeLite, InMemorySessionStore]:
    vocab = VocabLookup(settings.clarification.vocab_path)
    clarifier = clarifier or FakeClarifier()
    lite = lite or FakeLite()
    sessions = InMemorySessionStore(ttl_seconds=settings.session.ttl_seconds)
    orchestrator = Orchestrator(
        session_store=sessions,
        conventions_store=JsonFileConventionsStore(settings.clarification.conventions_path),
        intent_classifier=IntentClassifier(vocab),
        clarifier=clarifier,
        lite_client=lite,
        frontend_client=frontend or FakeFrontend(),
        fields_loader=JiraFieldsLoader(None, None),
        vocab=vocab,
        report_synthesiser=ReportSynthesiser(max_issues=settings.delivery.max_issues),
        delivery_channel=delivery or build_delivery_channel(settings.delivery),
        settings=settings,
    )
    return orchestrator, clarifier, lite, sessions


async def test_unambiguous_query_dispatches_directly(settings: Settings) -> None:
    orch, clarifier, lite, _ = make_orchestrator(settings)
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.requires_user_input is False
    assert response.jql == "project = CAR"
    assert lite.queries == ["list bugs in project CAR"]
    assert clarifier.formulate_calls == 0


async def test_ambiguous_query_returns_question(settings: Settings) -> None:
    orch, _clarifier, lite, sessions = make_orchestrator(settings)
    response = await orch.handle_query(query="show escalations", session_id="s1")
    assert response.requires_user_input is True
    assert response.clarification_question == QUESTION
    assert lite.queries == []
    session = await sessions.get("s1")
    assert session is not None
    assert session.state is ClarificationState.AWAITING_CLARIFICATION


async def test_answer_resolves_learns_and_dispatches(settings: Settings) -> None:
    orch, _, lite, _ = make_orchestrator(settings)
    await orch.handle_query(query="show escalations", session_id="s1")
    response = await orch.handle_query(
        query="show escalations", session_id="s1", clarification_answer="we use labels"
    )
    assert response.requires_user_input is False
    assert "labels = escalation" in lite.queries[0]  # interpretation hint appended
    assert [c.source for c in response.applied_conventions] == ["clarification"]
    # The convention was persisted for the team.
    store = JsonFileConventionsStore(settings.clarification.conventions_path)
    assert await store.get("_default", "escalation") is not None


async def test_learned_convention_skips_question(settings: Settings) -> None:
    orch, clarifier, _lite, _ = make_orchestrator(settings)
    await orch.handle_query(query="show escalations", session_id="s1")
    await orch.handle_query(
        query="show escalations", session_id="s1", clarification_answer="we use labels"
    )
    # A different session, same instance: never ask the same team twice (design doc).
    response = await orch.handle_query(query="show escalations", session_id="s2")
    assert response.requires_user_input is False
    assert clarifier.formulate_calls == 1
    assert [c.source for c in response.applied_conventions] == ["convention"]


async def test_elicitation_answers_inline(settings: Settings) -> None:
    orch, _, lite, _ = make_orchestrator(settings)

    async def elicit(question: str) -> str | None:
        assert question == QUESTION
        return "we use labels"

    response = await orch.handle_query(query="show escalations", session_id="s1", elicit=elicit)
    assert response.requires_user_input is False
    assert "labels = escalation" in lite.queries[0]


async def test_declined_elicitation_falls_back_to_round_trip(settings: Settings) -> None:
    orch, _, lite, _ = make_orchestrator(settings)

    async def elicit(question: str) -> str | None:
        return None  # host cannot elicit or user declined

    response = await orch.handle_query(query="show escalations", session_id="s1", elicit=elicit)
    assert response.requires_user_input is True
    assert lite.queries == []


async def test_repeated_call_reissues_cached_question(settings: Settings) -> None:
    orch, clarifier, _, _ = make_orchestrator(settings)
    first = await orch.handle_query(query="show escalations", session_id="s1")
    second = await orch.handle_query(query="show escalations", session_id="s1")
    assert second.clarification_question == first.clarification_question
    assert clarifier.formulate_calls == 1  # cached, no second LLM call


async def test_new_query_abandons_pending_clarification(settings: Settings) -> None:
    orch, _, lite, _ = make_orchestrator(settings)
    await orch.handle_query(query="show escalations", session_id="s1")
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.requires_user_input is False
    assert lite.queries == ["list bugs in project CAR"]


async def test_rounds_exhausted_dispatches_with_warning(settings: Settings) -> None:
    settings.clarification.max_rounds = 0
    orch, clarifier, lite, _ = make_orchestrator(settings)
    response = await orch.handle_query(query="show escalations", session_id="s1")
    assert response.requires_user_input is False
    assert clarifier.formulate_calls == 0
    assert any("clarification rounds exhausted" in e for e in response.errors)
    assert lite.queries == ["show escalations"]  # dispatched raw


async def test_clarifier_failure_degrades_to_raw_dispatch(settings: Settings) -> None:
    orch, _, lite, _ = make_orchestrator(settings, clarifier=FakeClarifier(fail=True))
    response = await orch.handle_query(query="show escalations", session_id="s1")
    assert response.requires_user_input is False
    assert any("clarification unavailable" in e for e in response.errors)
    assert lite.queries == ["show escalations"]


async def test_backend_failure_returns_errors_not_crash(settings: Settings) -> None:
    orch, _, _, _ = make_orchestrator(settings, lite=FakeLite(fail=True))
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.requires_user_input is False
    assert response.errors == ["backend down"]
    assert response.jql is None


async def test_session_resolutions_expire_with_session(settings: Settings, tmp_path: Path) -> None:
    # A fresh conventions file plus an expired session means the question is asked again.
    settings.clarification.conventions_path = tmp_path / "fresh-conventions.json"
    orch, clarifier, _, sessions = make_orchestrator(settings)
    await orch.handle_query(query="show escalations", session_id="s1")
    await sessions.delete("s1")
    await orch.handle_query(query="show escalations", session_id="s1")
    assert clarifier.formulate_calls == 2


async def test_show_in_ui_injects_raw_jql(settings: Settings) -> None:
    frontend = FakeFrontend()
    orch, _, _, _ = make_orchestrator(settings, frontend=frontend)
    response = await orch.handle_query(
        query="list bugs in project CAR", session_id="s1", show_in_ui=True
    )
    assert frontend.injected == ["project = CAR /raw"]
    assert response.ui_injected is True
    assert response.errors == []


async def test_show_in_ui_is_opt_in(settings: Settings) -> None:
    frontend = FakeFrontend()
    orch, _, _, _ = make_orchestrator(settings, frontend=frontend)
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert frontend.injected == []
    assert response.ui_injected is False


async def test_show_in_ui_rejection_degrades_with_note(settings: Settings) -> None:
    frontend = FakeFrontend(accept=False)
    orch, _, _, _ = make_orchestrator(settings, frontend=frontend)
    response = await orch.handle_query(
        query="list bugs in project CAR", session_id="s1", show_in_ui=True
    )
    assert response.ui_injected is False
    assert response.jql == "project = CAR"  # query result is unaffected
    assert any("chart not shown in UI" in e for e in response.errors)


async def test_show_in_ui_skipped_when_backend_fails(settings: Settings) -> None:
    frontend = FakeFrontend()
    orch, _, _, _ = make_orchestrator(settings, lite=FakeLite(fail=True), frontend=frontend)
    response = await orch.handle_query(
        query="list bugs in project CAR", session_id="s1", show_in_ui=True
    )
    assert frontend.injected == []
    assert response.ui_injected is False


async def test_show_in_ui_without_jql_adds_note(settings: Settings) -> None:
    frontend = FakeFrontend()
    orch, _, _, _ = make_orchestrator(settings, lite=FakeLite(jql=None), frontend=frontend)
    response = await orch.handle_query(
        query="list bugs in project CAR", session_id="s1", show_in_ui=True
    )
    assert frontend.injected == []
    assert any("no JQL was generated" in e for e in response.errors)


class BrokenDeliveryChannel(BaseDeliveryChannel):
    async def deliver(self, *, report_id: str, content: str) -> str:
        raise OSError("disk full")


async def test_report_written_for_every_dispatch(settings: Settings) -> None:
    orch, _, _, _ = make_orchestrator(settings)
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.report_path is not None
    content = Path(response.report_path).read_text(encoding="utf-8")
    assert "list bugs in project CAR" in content
    assert "project = CAR" in content  # the generated JQL is in the report


async def test_report_written_even_when_backend_fails(settings: Settings) -> None:
    orch, _, _, _ = make_orchestrator(settings, lite=FakeLite(fail=True))
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.report_path is not None
    content = Path(response.report_path).read_text(encoding="utf-8")
    assert "backend down" in content  # the failure itself is human-verifiable


async def test_no_report_for_clarification_questions(settings: Settings) -> None:
    orch, _, _, _ = make_orchestrator(settings)
    response = await orch.handle_query(query="show escalations", session_id="s1")
    assert response.requires_user_input is True
    assert response.report_path is None
    assert not settings.delivery.output_dir.exists()


async def test_report_disabled_by_settings(settings: Settings) -> None:
    settings.delivery.enabled = False
    orch, _, _, _ = make_orchestrator(settings)
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.report_path is None
    assert not settings.delivery.output_dir.exists()


async def test_report_delivery_failure_degrades(settings: Settings) -> None:
    orch, _, _, _ = make_orchestrator(settings, delivery=BrokenDeliveryChannel())
    response = await orch.handle_query(query="list bugs in project CAR", session_id="s1")
    assert response.jql == "project = CAR"  # the query result is unaffected
    assert response.report_path is None
    assert any("report not written" in e for e in response.errors)
