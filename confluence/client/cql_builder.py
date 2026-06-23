"""CQL variant builder - pure function, no I/O.

Produces three named CQL strings from a QueryIntent + space list. Named fields
on CqlVariants allow log lines to say "variant title_review returned 3 pages"
instead of "variant 0 returned 3 pages".
"""

from dataclasses import dataclass

from confluence.models.intent import QueryIntent


@dataclass(frozen=True)
class CqlVariants:
    """Three parallel CQL search strings for a Confluence research pass."""

    title_review: str
    title_version: str
    text_version: str

    def as_list(self) -> list[str]:
        return [self.title_review, self.title_version, self.text_version]


def build_cql_variants(
    intent: QueryIntent,
    spaces: list[str],
    recency_days: int = 30,
) -> CqlVariants:
    """Return three CQL variant strings for a given QueryIntent.

    Args:
        intent: Extracted intent with version_refs and confluence_keywords.
        spaces: Confluence space keys to restrict the search.
        recency_days: Days back for lastModified filter on variants 1 and 3.

    Returns:
        CqlVariants with three named CQL strings.
        Falls back to a broad keyword search when version_refs is empty.
    """
    space_filter = _space_filter(spaces)

    if intent.version_refs:
        version_short = _version_short(intent.version_refs[0])
        version_abbrev = version_short.split(".")[0]
    else:
        version_short = intent.confluence_keywords[0] if intent.confluence_keywords else ""
        version_abbrev = version_short

    title_review = (
        f'title ~ "Review" AND {space_filter} AND lastModified > now("-{recency_days}d")'
        if space_filter
        else f'title ~ "Review" AND lastModified > now("-{recency_days}d")'
    )

    if version_abbrev:
        title_version = (
            f'title ~ "{version_abbrev}" AND text ~ "blocker" AND {space_filter}'
            if space_filter
            else f'title ~ "{version_abbrev}" AND text ~ "blocker"'
        )
    else:
        title_version = (
            f'text ~ "blocker" AND {space_filter}'
            if space_filter
            else 'text ~ "blocker"'
        )

    if version_short:
        text_version = (
            (
                f'text ~ "{version_short}" AND {space_filter}'
                f' AND lastModified > now("-{recency_days}d")'
            )
            if space_filter
            else f'text ~ "{version_short}" AND lastModified > now("-{recency_days}d")'
        )
    else:
        keywords = " OR ".join(
            f'"{kw}"' for kw in intent.confluence_keywords[:3]
        )
        text_version = (
            f"text ~ ({keywords}) AND {space_filter}"
            if keywords and space_filter
            else f"text ~ ({keywords})" if keywords else title_review
        )

    return CqlVariants(
        title_review=title_review,
        title_version=title_version,
        text_version=text_version,
    )


def _space_filter(spaces: list[str]) -> str:
    """Return a CQL space IN (...) clause, or empty string when spaces is empty."""
    if not spaces:
        return ""
    quoted = ", ".join(f'"{s}"' for s in spaces)
    return f"space IN ({quoted})"


def _version_short(version_ref: str) -> str:
    """Extract the short version string from a full version ref.

    'ACME_R1.0' -> 'R1.0'
    'R1.0'      -> 'R1.0'
    'R1'        -> 'R1'
    """
    return version_ref.split("_")[-1]
