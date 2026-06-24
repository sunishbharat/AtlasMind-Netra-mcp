"""Tests for ``config.settings`` - env var parsing for list-typed fields.

Regression coverage for the ``SettingsError`` raised on
``confluence.default_spaces`` and ``analysis.blocked_statuses`` when env vars
arrive empty or as comma-separated strings (the documented format in
``manifest.yml.template`` and ``.env.example``).

The fix pairs ``Annotated[list[str], NoDecode]`` with a ``field_validator(mode="before")``
that calls ``_parse_csv_or_json_list``. These tests pin down both halves:

* the helper itself (edge cases that are awkward to exercise via ``Settings()``);
* ``Settings()`` end-to-end through env vars and direct kwargs.

No live network or LLM calls - these tests only construct settings objects.
"""

from __future__ import annotations

import pytest

from config.settings import Settings, _parse_csv_or_json_list

# --------------------------------------------------------------------------- #
# Direct helper tests
# --------------------------------------------------------------------------- #


class TestParseCsvOrJsonList:
    """Unit tests for ``_parse_csv_or_json_list`` - the shared list-coercion helper."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            # Empty / whitespace inputs collapse to an empty list.
            pytest.param("", [], id="empty-string"),
            pytest.param("   ", [], id="whitespace-only"),
            pytest.param("\n\t", [], id="newlines-and-tabs"),
            # CSV-style inputs (the documented env-var format).
            pytest.param("a", ["a"], id="single-value-no-comma"),
            pytest.param("a,b", ["a", "b"], id="csv-two-items"),
            pytest.param("a,b,c", ["a", "b", "c"], id="csv-three-items"),
            pytest.param("a, b", ["a", "b"], id="csv-with-internal-spaces"),
            pytest.param(" a , b ", ["a", "b"], id="csv-with-surrounding-spaces"),
            pytest.param(",a,", ["a"], id="csv-leading-and-trailing-commas"),
            pytest.param("a,,b", ["a", "b"], id="csv-double-comma"),
            pytest.param("a,\n", ["a"], id="csv-trailing-newline"),
            # JSON-array inputs (also supported).
            pytest.param("[]", [], id="json-empty-array"),
            pytest.param('["a"]', ["a"], id="json-single-item"),
            pytest.param('["a", "b"]', ["a", "b"], id="json-two-items"),
            pytest.param('["a", "b", "c"]', ["a", "b", "c"], id="json-three-items"),
            pytest.param('[" a ", " b "]', ["a", "b"], id="json-array-trimmed"),
            pytest.param('["a", 1]', ["a", "1"], id="json-array-int-coerced-to-str"),
            pytest.param('["", "a"]', ["a"], id="json-array-empty-string-filtered"),
            pytest.param('["a", ""]', ["a"], id="json-array-trailing-empty-filtered"),
            # Malformed JSON that starts with ``[`` falls through to CSV parsing.
            pytest.param("[invalid", ["[invalid"], id="malformed-json-no-comma"),
            pytest.param("[a, b]", ["[a", "b]"], id="malformed-json-with-comma"),
            # Pass-through inputs (list / None / other scalars).
            pytest.param([], [], id="actual-empty-list"),
            pytest.param(["a", "b"], ["a", "b"], id="actual-list"),
            pytest.param([" a ", ""], ["a"], id="actual-list-trimmed-and-filtered"),
            pytest.param([1, 2], ["1", "2"], id="actual-list-int-coerced"),
            pytest.param(None, None, id="none-passthrough"),
            pytest.param(42, 42, id="int-passthrough"),
            pytest.param({"a": 1}, {"a": 1}, id="dict-passthrough"),
        ],
    )
    def test_parses_value(self, value: object, expected: object) -> None:
        assert _parse_csv_or_json_list(value) == expected


# --------------------------------------------------------------------------- #
# Settings() integration - Confluence.default_spaces
# --------------------------------------------------------------------------- #


class TestConfluenceDefaultSpacesEnvVar:
    """``NETRA_CONFLUENCE__DEFAULT_SPACES`` must not crash ``Settings()``."""

    def test_unset_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NETRA_CONFLUENCE__DEFAULT_SPACES", raising=False)
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == []

    def test_empty_string_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty env var must not raise ``SettingsError`` (the original CF crash).

        With ``env_ignore_empty=True`` set in ``SettingsConfigDict``, pydantic-settings
        drops the empty value before it reaches the validator, so ``default_factory=list``
        yields ``[]``.
        """
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == []

    def test_whitespace_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whitespace-only env var must not raise.

        ``env_ignore_empty`` checks ``v == ""`` strictly, so whitespace reaches
        the validator. Without ``NoDecode``, ``json.loads(" ")`` raises - this test
        pins down the validator handling.
        """
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "   ")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == []

    def test_csv_string_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The documented CSV format in ``manifest.yml.template`` must parse.

        Without ``NoDecode``, this raises ``JSONDecodeError`` because ``"ENG,OPS"``
        is not a valid JSON array. This is the second shape of the original crash.
        """
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "ENG,OPS")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]

    def test_csv_with_spaces_trims(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", " ENG , OPS ")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]

    def test_csv_three_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "ENG,OPS,QA")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS", "QA"]

    def test_json_array_string_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", '["ENG","OPS"]')
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]

    def test_single_space_key_csv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A single key (no comma) must still parse as a one-element list."""
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "ENG")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG"]

    def test_other_confluence_fields_unaffected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``NoDecode`` + validator on ``default_spaces`` must not bleed into other fields."""
        monkeypatch.delenv("NETRA_CONFLUENCE__DEFAULT_SPACES", raising=False)
        monkeypatch.setenv("NETRA_CONFLUENCE__SEARCH_LIMIT", "20")
        monkeypatch.setenv("NETRA_CONFLUENCE__MAX_PAGES_TOTAL", "12")
        monkeypatch.setenv("NETRA_CONFLUENCE__RECENCY_DAYS", "7")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == []
        assert settings.confluence.search_limit == 20
        assert settings.confluence.max_pages_total == 12
        assert settings.confluence.recency_days == 7

    def test_confluence_unconfigured_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no ``NETRA_CONFLUENCE__*`` vars are set, Confluence is disabled.

        All optional string fields are ``None`` and ``default_spaces`` is empty.
        This is the production default - the feature is opt-in.
        """
        for var in (
            "NETRA_CONFLUENCE__BASE_URL",
            "NETRA_CONFLUENCE__API_TOKEN",
            "NETRA_CONFLUENCE__EMAIL",
            "NETRA_CONFLUENCE__DEFAULT_SPACES",
        ):
            monkeypatch.delenv(var, raising=False)
        settings = Settings(_env_file=None)
        assert settings.confluence.base_url is None
        assert settings.confluence.api_token is None
        assert settings.confluence.email is None
        assert settings.confluence.default_spaces == []


# --------------------------------------------------------------------------- #
# Settings() integration - Analysis.blocked_statuses
# --------------------------------------------------------------------------- #


_DEFAULT_BLOCKED_STATUSES = ["Blocked", "Stalled", "On Hold", "Waiting"]


class TestBlockedStatusesEnvVar:
    """``NETRA_ANALYSIS__BLOCKED_STATUSES`` must not crash ``Settings()``."""

    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NETRA_ANALYSIS__BLOCKED_STATUSES", raising=False)
        settings = Settings(_env_file=None)
        assert settings.analysis.blocked_statuses == _DEFAULT_BLOCKED_STATUSES

    def test_empty_string_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty env var must not raise; falls back to the documented default list."""
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", "")
        settings = Settings(_env_file=None)
        assert settings.analysis.blocked_statuses == _DEFAULT_BLOCKED_STATUSES

    def test_whitespace_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whitespace is non-empty by env_ignore_empty's strict equality check.

        ``env_ignore_empty`` checks ``v == ""`` strictly, so whitespace reaches
        the validator - the helper strips and returns ``[]``. The test pins down
        the interaction: the default list is overridden (because the value is
        non-empty), so the user gets an empty blocked-status set, not the default.
        """
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", "   ")
        settings = Settings(_env_file=None)
        assert settings.analysis.blocked_statuses == []

    def test_csv_string_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", "On Hold,Waiting")
        settings = Settings(_env_file=None)
        assert settings.analysis.blocked_statuses == ["On Hold", "Waiting"]

    def test_csv_with_spaces_trims(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", " On Hold , Waiting ")
        settings = Settings(_env_file=None)
        assert settings.analysis.blocked_statuses == ["On Hold", "Waiting"]

    def test_json_array_string_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", '["On Hold", "Waiting"]')
        settings = Settings(_env_file=None)
        assert settings.analysis.blocked_statuses == ["On Hold", "Waiting"]


# --------------------------------------------------------------------------- #
# Regression coverage - the exact crash from the deployed log
# --------------------------------------------------------------------------- #


class TestRegressionEmptyConfluenceEnv:
    """Reproduce the deployed failure mode from 2026-06-23 CF logs.

    Log snippet::

        pydantic_settings.exceptions.SettingsError: error parsing value for
            field "confluence" from source "EnvSettingsSource"
        json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)
    """

    def test_empty_default_spaces_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``NETRA_CONFLUENCE__DEFAULT_SPACES=""`` must not raise."""
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "")
        # Must not raise SettingsError or JSONDecodeError.
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == []

    def test_csv_default_spaces_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``NETRA_CONFLUENCE__DEFAULT_SPACES=ENG,OPS`` must not raise.

        Pre-NoDecode this crashed because pydantic-settings called
        ``json.loads("ENG,OPS")`` on the non-JSON CSV string.
        """
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "ENG,OPS")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]


# --------------------------------------------------------------------------- #
# Combined scenarios
# --------------------------------------------------------------------------- #


class TestBothListFieldsConfiguredTogether:
    """Both list-typed fields configured simultaneously must coexist."""

    def test_both_csv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "ENG,OPS")
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", "On Hold,Waiting")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]
        assert settings.analysis.blocked_statuses == ["On Hold", "Waiting"]

    def test_mixed_csv_and_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", '["ENG","OPS"]')
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", "On Hold,Waiting")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]
        assert settings.analysis.blocked_statuses == ["On Hold", "Waiting"]

    def test_one_csv_one_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty on one field, CSV on the other - both must parse independently."""
        monkeypatch.setenv("NETRA_CONFLUENCE__DEFAULT_SPACES", "ENG,OPS")
        monkeypatch.setenv("NETRA_ANALYSIS__BLOCKED_STATUSES", "")
        settings = Settings(_env_file=None)
        assert settings.confluence.default_spaces == ["ENG", "OPS"]
        assert settings.analysis.blocked_statuses == _DEFAULT_BLOCKED_STATUSES


# --------------------------------------------------------------------------- #
# Direct construction (kwargs, not env vars)
# --------------------------------------------------------------------------- #


class TestDirectConstruction:
    """The validators also run when lists are passed directly to ``Settings()``."""

    def test_default_spaces_via_kwargs(self) -> None:
        settings = Settings(
            _env_file=None,
            confluence={"default_spaces": ["ENG", "OPS"]},  # type: ignore[arg-type]
        )
        assert settings.confluence.default_spaces == ["ENG", "OPS"]

    def test_blocked_statuses_via_kwargs(self) -> None:
        settings = Settings(
            _env_file=None,
            analysis={"blocked_statuses": ["On Hold", "Waiting"]},  # type: ignore[arg-type]
        )
        assert settings.analysis.blocked_statuses == ["On Hold", "Waiting"]

    def test_default_spaces_csv_string_via_kwargs(self) -> None:
        """The validator also coerces strings when passed as Python args.

        This matters for callers that hand-build a settings dict and pass a string
        by mistake - they still get a list back instead of a crash.
        """
        settings = Settings(
            _env_file=None,
            confluence={"default_spaces": "ENG,OPS"},  # type: ignore[arg-type]
        )
        assert settings.confluence.default_spaces == ["ENG", "OPS"]

    def test_default_spaces_empty_list_via_kwargs(self) -> None:
        settings = Settings(
            _env_file=None,
            confluence={"default_spaces": []},  # type: ignore[arg-type]
        )
        assert settings.confluence.default_spaces == []
