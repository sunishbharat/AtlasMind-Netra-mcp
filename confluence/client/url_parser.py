"""Parse Confluence page IDs from page URLs."""
import re
from urllib.parse import parse_qs, urlparse

# Require the numeric ID to be a standalone path segment (followed by / ? # or end of string)
# to prevent accidental matches inside longer path components.
_PATH_ID_RE = re.compile(r"/pages/(\d+)(?:[/?#]|$)")


def extract_page_id(url: str) -> str | None:
    """Return the numeric page ID from a Confluence URL, or None if not extractable.

    Supports Cloud (/pages/{id}/) and Server viewpage (?pageId={id}) formats.
    Server display-style URLs (/display/SPACE/Title) return None.
    """
    m = _PATH_ID_RE.search(url)
    if m:
        return m.group(1)
    params = parse_qs(urlparse(url).query)
    ids = params.get("pageId")
    return ids[0] if ids else None
