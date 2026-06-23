"""HTML stripping and section extraction - pure functions, stdlib only.

No BeautifulSoup4 or lxml required. html.parser is sufficient for
Confluence body.view output (pre-rendered HTML, no malformed markup).
"""

import re
from html.parser import HTMLParser


class _StripHTMLParser(HTMLParser):
    """Accumulate text nodes, discard all tags."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    def result(self) -> str:
        return " ".join(self._parts)


def strip_html(html: str) -> str:
    """Strip all HTML tags and return plain text with whitespace normalised."""
    parser = _StripHTMLParser()
    parser.feed(html)
    return parser.result()


_HEADING_RE = re.compile(
    r"<h[1-6][^>]*>(.*?)</h[1-6]>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def extract_sections(
    html: str,
    target_headings: list[str],
    max_chars: int = 10_000,
) -> dict[str, str]:
    """Extract plain-text content for each target heading from Confluence HTML.

    Searches for headings case-insensitively. Returns a dict mapping each matched
    heading (as it appears in the page) to the plain text of that section, capped
    at max_chars. Headings not found in the page are absent from the result.

    Args:
        html: Raw HTML from Confluence body.view.
        target_headings: Headings to search for, e.g. ['At Risk', 'Blocked', 'Mitigation'].
        max_chars: Maximum characters of content to return per section.

    Returns:
        Dict of heading -> plain text content.
    """
    if not target_headings:
        return {}

    target_lower = {h.lower(): h for h in target_headings}
    sections: dict[str, str] = {}

    # Split HTML on heading tags to get (heading_text, following_html) pairs.
    # Strategy: find all heading positions, then slice content between them.
    heading_matches = list(_HEADING_RE.finditer(html))
    if not heading_matches:
        return {}

    for idx, match in enumerate(heading_matches):
        raw_heading = _TAG_RE.sub("", match.group(1)).strip()
        normalised = raw_heading.lower()

        original_heading = target_lower.get(normalised)
        if original_heading is None:
            continue

        # Content runs from end of this heading tag to start of next heading tag.
        content_start = match.end()
        content_end = (
            heading_matches[idx + 1].start()
            if idx + 1 < len(heading_matches)
            else len(html)
        )
        section_html = html[content_start:content_end]
        plain = strip_html(section_html)
        sections[original_heading] = plain[:max_chars]

    return sections
