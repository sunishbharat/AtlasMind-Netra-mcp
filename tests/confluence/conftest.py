"""Shared fixtures for confluence tests."""

import pytest

from confluence.models.page import ConfluencePage


@pytest.fixture
def sample_page() -> ConfluencePage:
    return ConfluencePage(
        page_id="123456",
        title="CC INCR Review | CW25",
        space_key="PROJ",
        url="https://confluence.example.com/pages/123456",
        last_modified="2026-06-20T10:00:00.000Z",
        cql_excerpt="PI Assessment blocker tracking for E035",
    )


@pytest.fixture
def sample_html_page() -> str:
    return """
    <h2>At Risk</h2>
    <p>CAR-101 is blocked waiting on vendor response.</p>
    <h2>Mitigation</h2>
    <p>Escalation call scheduled for 2026-06-25. Owner: jdoe.</p>
    <h2>Resolved</h2>
    <p>CAR-050 was resolved last week.</p>
    """
