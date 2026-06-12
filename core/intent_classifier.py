"""Deterministic ambiguous-term detection (design doc: IntentClassifier).

No LLM involved: detection is a case-insensitive word-boundary match of the vocabulary
terms, so the same query always yields the same terms.
"""

import re

from core.vocab_lookup import VocabLookup


class IntentClassifier:
    """Detects ambiguous vocabulary terms in a natural language query."""

    def __init__(self, vocab: VocabLookup) -> None:
        # Allow a trailing plural ("escalations", "blockers") but require word boundaries,
        # so "open" never matches inside "opened".
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            (term, re.compile(rf"\b{re.escape(term)}(?:e?s)?\b", re.IGNORECASE))
            for term in vocab.terms
        ]

    def detect(self, text: str) -> list[str]:
        """Return the ambiguous terms present in `text`, in vocabulary order."""
        return [term for term, pattern in self._patterns if pattern.search(text)]
