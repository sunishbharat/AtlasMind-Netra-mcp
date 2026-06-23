"""Tests for confluence.client.html_extractor - pure function, no mocks, no async."""


from confluence.client.html_extractor import extract_sections, strip_html


class TestStripHtml:
    def test_removes_tags(self) -> None:
        assert strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_whitespace_normalised(self) -> None:
        result = strip_html("<p>  foo  </p><p>  bar  </p>")
        assert result == "foo bar"

    def test_empty_input(self) -> None:
        assert strip_html("") == ""

    def test_no_tags(self) -> None:
        assert strip_html("plain text") == "plain text"

    def test_nested_tags(self) -> None:
        result = strip_html("<div><ul><li>item 1</li><li>item 2</li></ul></div>")
        assert result == "item 1 item 2"

    def test_attributes_stripped(self) -> None:
        result = strip_html('<a href="https://example.com" class="link">click</a>')
        assert result == "click"


class TestExtractSections:
    def test_extracts_matching_heading(self) -> None:
        html = "<h2>At Risk</h2><p>CAR-101 is blocked.</p><h2>Other</h2><p>Irrelevant.</p>"
        sections = extract_sections(html, ["At Risk"])
        assert "At Risk" in sections
        assert "CAR-101" in sections["At Risk"]

    def test_case_insensitive_heading_match(self) -> None:
        html = "<h2>AT RISK</h2><p>blocked issue here</p>"
        sections = extract_sections(html, ["At Risk"])
        assert "At Risk" in sections

    def test_section_ends_at_next_heading(self) -> None:
        html = (
            "<h2>Blocked</h2><p>CAR-101</p>"
            "<h2>Mitigation</h2><p>CAR-202 escalated</p>"
        )
        sections = extract_sections(html, ["Blocked", "Mitigation"])
        assert "CAR-101" in sections["Blocked"]
        assert "CAR-202" in sections["Mitigation"]
        assert "CAR-202" not in sections["Blocked"]

    def test_missing_heading_absent_from_result(self) -> None:
        html = "<h2>At Risk</h2><p>content</p>"
        sections = extract_sections(html, ["At Risk", "Missing"])
        assert "At Risk" in sections
        assert "Missing" not in sections

    def test_no_headings_returns_empty(self) -> None:
        html = "<p>just a paragraph</p>"
        sections = extract_sections(html, ["At Risk"])
        assert sections == {}

    def test_empty_target_headings_returns_empty(self) -> None:
        html = "<h2>Something</h2><p>content</p>"
        sections = extract_sections(html, [])
        assert sections == {}

    def test_max_chars_truncates_content(self) -> None:
        html = "<h2>At Risk</h2><p>" + "x" * 500 + "</p>"
        sections = extract_sections(html, ["At Risk"], max_chars=100)
        assert len(sections["At Risk"]) <= 100

    def test_h3_heading_matched(self) -> None:
        html = "<h3>Mitigation</h3><p>action item here</p>"
        sections = extract_sections(html, ["Mitigation"])
        assert "Mitigation" in sections
        assert "action item here" in sections["Mitigation"]

    def test_last_section_extends_to_end(self) -> None:
        html = "<h2>Notes</h2><p>final content</p>"
        sections = extract_sections(html, ["Notes"])
        assert "final content" in sections["Notes"]
