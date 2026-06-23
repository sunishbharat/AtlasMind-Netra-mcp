"""Tests for confluence.client.url_parser.extract_page_id."""

import pytest

from confluence.client.url_parser import extract_page_id


class TestExtractPageId:
    @pytest.mark.parametrize(
        "url,expected",
        [
            (
                "https://mycompany.atlassian.net/wiki/spaces/PROJ/pages/123456/Page-Title",
                "123456",
            ),
            (
                "https://mycompany.atlassian.net/wiki/spaces/PROJ/pages/987654321/Another-Page",
                "987654321",
            ),
        ],
    )
    def test_cloud_url_returns_id(self, url: str, expected: str) -> None:
        assert extract_page_id(url) == expected

    def test_server_viewpage_returns_id(self) -> None:
        url = "https://confluence.example.com/pages/viewpage.action?pageId=123456"
        assert extract_page_id(url) == "123456"

    def test_display_style_returns_none(self) -> None:
        url = "https://confluence.example.com/display/PROJ/Page+Title"
        assert extract_page_id(url) is None

    def test_arbitrary_url_returns_none(self) -> None:
        assert extract_page_id("https://example.com/some/path") is None

    def test_empty_string_returns_none(self) -> None:
        assert extract_page_id("") is None
