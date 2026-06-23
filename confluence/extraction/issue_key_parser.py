"""Jira issue key extraction from plain text - pure function, compiled regex.

False-positive filter removes common all-caps abbreviations that match the
PROJECT-NNN pattern but are not Jira keys.
"""

import re

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]*_?[A-Z0-9]+-\d+)\b")

# Common false positives: HTTP status codes, CSS selectors, acronyms used inline.
_FALSE_POSITIVES: frozenset[str] = frozenset(
    {
        "HTTP-200",
        "HTTP-201",
        "HTTP-400",
        "HTTP-401",
        "HTTP-403",
        "HTTP-404",
        "HTTP-422",
        "HTTP-500",
        "HTTP-503",
        "CSS-1",
        "CSS-2",
        "CSS-3",
        "API-1",
        "API-2",
        "RFC-7231",
        "RFC-6749",
        "ISO-8601",
        "UTF-8",
        "UTF-16",
        "SHA-256",
        "MD-5",
    }
)


def extract_issue_keys(text: str) -> list[str]:
    """Extract unique Jira issue keys from plain text, preserving first-seen order.

    Applies a false-positive filter to remove common abbreviations that match
    the PROJECT-NNN pattern but are not Jira keys.

    Args:
        text: Plain text (HTML already stripped) to search.

    Returns:
        Deduplicated list of Jira issue key strings in first-seen order.
    """
    seen: dict[str, None] = {}  # insertion-ordered set
    for match in _JIRA_KEY_RE.finditer(text):
        key = match.group(1)
        if key not in _FALSE_POSITIVES and key not in seen:
            seen[key] = None
    return list(seen)
