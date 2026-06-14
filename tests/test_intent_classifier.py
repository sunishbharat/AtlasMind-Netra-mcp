"""IntentClassifier: deterministic term detection."""

from core.intent_classifier import IntentClassifier
from core.vocab_lookup import VocabLookup


def test_detects_terms_and_plurals(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    assert classifier.detect("show escalations from today") == ["escalation", "today"]


def test_detects_multi_word_terms(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    assert classifier.detect("what is my team working on") == ["my team"]


def test_detection_is_case_insensitive(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    assert classifier.detect("Show OPEN Bugs") == ["open"]


def test_no_match_inside_longer_words(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    # "opened" must not trigger "open"; "unblocked" must not trigger "blocker".
    assert classifier.detect("issues opened and unblocked yesterday") == []


def test_unambiguous_query_yields_nothing(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    assert classifier.detect("list bugs in project CAR") == []


def test_detects_blocker_plural(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    assert classifier.detect("highlight newly created blockers") == ["blocker"]


def test_detects_status_report_phrase(vocab: VocabLookup) -> None:
    classifier = IntentClassifier(vocab)
    assert classifier.detect("generate a status report for last week") == ["status report"]
