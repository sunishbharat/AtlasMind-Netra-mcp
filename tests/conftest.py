"""Shared test fixtures. No live network: model requests are globally forbidden."""

from pathlib import Path

import pydantic_ai.models
import pytest

from config.settings import Settings
from core.vocab_lookup import VocabLookup

# Any accidental real LLM call fails loudly instead of spending money (PydanticAI guard).
pydantic_ai.models.ALLOW_MODEL_REQUESTS = False

REPO_ROOT = Path(__file__).resolve().parent.parent
VOCAB_PATH = REPO_ROOT / "config" / "clarification_vocab.json"
PROMPT_PATH = REPO_ROOT / "prompts" / "clarification_prompt.md"


@pytest.fixture
def vocab() -> VocabLookup:
    """The real shipped vocabulary - tests should exercise what production loads."""
    return VocabLookup(VOCAB_PATH)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Defaults with all writable paths redirected into tmp_path; .env ignored."""
    s = Settings(_env_file=None)
    s.clarification.conventions_path = tmp_path / "conventions.json"
    s.clarification.vocab_path = VOCAB_PATH
    s.clarification.prompt_path = PROMPT_PATH
    s.delivery.output_dir = tmp_path / "reports"
    return s
