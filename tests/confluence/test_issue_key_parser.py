"""Tests for confluence.extraction.issue_key_parser - pure function, no mocks, no async."""


from confluence.extraction.issue_key_parser import extract_issue_keys


class TestExtractIssueKeys:
    def test_extracts_simple_key(self) -> None:
        assert extract_issue_keys("See CAR-101 for details.") == ["CAR-101"]

    def test_extracts_multiple_keys(self) -> None:
        result = extract_issue_keys("CAR-101 blocks CAR-205.")
        assert result == ["CAR-101", "CAR-205"]

    def test_deduplicates_keys(self) -> None:
        result = extract_issue_keys("CAR-101 is blocked. Also CAR-101 was flagged.")
        assert result == ["CAR-101"]

    def test_preserves_first_seen_order(self) -> None:
        result = extract_issue_keys("See CAR-205 and CAR-101.")
        assert result == ["CAR-205", "CAR-101"]

    def test_underscore_in_project_key(self) -> None:
        result = extract_issue_keys("PROJ_A-999 is at risk.")
        assert "PROJ_A-999" in result

    def test_false_positive_http_status_filtered(self) -> None:
        result = extract_issue_keys("Returned HTTP-404 and HTTP-503.")
        assert result == []

    def test_false_positive_rfc_filtered(self) -> None:
        result = extract_issue_keys("As per RFC-7231 section 4.")
        assert result == []

    def test_mixed_real_and_false_positive(self) -> None:
        result = extract_issue_keys("CAR-101 returned HTTP-200.")
        assert result == ["CAR-101"]

    def test_empty_text_returns_empty(self) -> None:
        assert extract_issue_keys("") == []

    def test_no_keys_in_text(self) -> None:
        assert extract_issue_keys("No issue keys here, just plain prose.") == []

    def test_keys_in_table_row(self) -> None:
        text = "| CAR-101 | Blocked | jdoe | escalation call |"
        result = extract_issue_keys(text)
        assert result == ["CAR-101"]

    def test_multiline_text(self) -> None:
        text = "First: CAR-101\nSecond: CAR-202\nThird: CAR-303"
        result = extract_issue_keys(text)
        assert result == ["CAR-101", "CAR-202", "CAR-303"]

    def test_returns_list_not_set(self) -> None:
        result = extract_issue_keys("CAR-101 CAR-202")
        assert isinstance(result, list)
