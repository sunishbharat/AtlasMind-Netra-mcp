"""Tests for confluence.client.cql_builder - pure function, no mocks, no async."""


from confluence.client.cql_builder import _space_filter, _version_short, build_cql_variants
from confluence.models.intent import QueryIntent


def _intent(
    version_refs: list[str] | None = None,
    confluence_keywords: list[str] | None = None,
    intent_type: str = "release_risk",
) -> QueryIntent:
    return QueryIntent(
        version_refs=version_refs or [],
        confluence_keywords=confluence_keywords or [],
        intent_type=intent_type,  # type: ignore[arg-type]
    )


class TestVersionShort:
    def test_strips_platform_prefix(self) -> None:
        assert _version_short("ACME_R1.0") == "R1.0"

    def test_no_prefix_unchanged(self) -> None:
        assert _version_short("R1.0") == "R1.0"

    def test_no_dot_unchanged(self) -> None:
        assert _version_short("R1") == "R1"


class TestSpaceFilter:
    def test_empty_spaces_returns_empty_string(self) -> None:
        assert _space_filter([]) == ""

    def test_single_space(self) -> None:
        assert _space_filter(["PROJ"]) == 'space IN ("PROJ")'

    def test_multiple_spaces(self) -> None:
        result = _space_filter(["PROJ", "TEAM"])
        assert result == 'space IN ("PROJ", "TEAM")'


class TestBuildCqlVariants:
    def test_three_variants_produced(self) -> None:
        intent = _intent(version_refs=["ACME_R1.0"])
        variants = build_cql_variants(intent, spaces=["PROJ"])
        assert len(variants.as_list()) == 3

    def test_title_review_contains_recency_filter(self) -> None:
        intent = _intent(version_refs=["ACME_R1.0"])
        variants = build_cql_variants(intent, spaces=["PROJ"], recency_days=14)
        assert 'now("-14d")' in variants.title_review

    def test_title_version_contains_version_abbrev(self) -> None:
        intent = _intent(version_refs=["ACME_R1.0"])
        variants = build_cql_variants(intent, spaces=["PROJ"])
        assert "R1" in variants.title_version
        assert "blocker" in variants.title_version

    def test_text_version_contains_short_version(self) -> None:
        intent = _intent(version_refs=["ACME_R1.0"])
        variants = build_cql_variants(intent, spaces=["PROJ"])
        assert "R1.0" in variants.text_version

    def test_space_filter_applied(self) -> None:
        intent = _intent(version_refs=["E035"])
        variants = build_cql_variants(intent, spaces=["PROJ", "TEAM"])
        for cql in variants.as_list():
            assert "PROJ" in cql

    def test_no_spaces_omits_space_filter(self) -> None:
        intent = _intent(version_refs=["E035"])
        variants = build_cql_variants(intent, spaces=[])
        for cql in variants.as_list():
            assert "space IN" not in cql

    def test_no_version_refs_uses_keywords(self) -> None:
        intent = _intent(version_refs=[], confluence_keywords=["blocker", "E035"])
        variants = build_cql_variants(intent, spaces=["PROJ"])
        # Must not raise; must produce 3 strings
        assert len(variants.as_list()) == 3

    def test_all_variants_are_non_empty_strings(self) -> None:
        intent = _intent(version_refs=["E035"])
        variants = build_cql_variants(intent, spaces=["PROJ"])
        for cql in variants.as_list():
            assert isinstance(cql, str)
            assert len(cql) > 0
