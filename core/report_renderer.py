"""HTML renderer for markdown report files.

Strategy pattern: swap this class to change the report output format (Jinja2, PDF, etc.)
without touching the orchestrator or route handler. Inject via Orchestrator.__init__.
"""

import markdown as md_lib

# Module-level template - .format() placeholders are {report_id} and {body}.
# {{ }} double-braces in the CSS are literal brace characters after .format() expansion.
_REPORT_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Report {report_id}</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 960px;
            margin: 2rem auto; padding: 0 1.5rem; line-height: 1.6; color: #1f2328; }}
    h1, h2, h3 {{ border-bottom: 1px solid #d0d7de; padding-bottom: .3rem; }}
    pre {{ background: #f6f8fa; padding: 1rem; border-radius: 6px;
           overflow-x: auto; font-size: .9em; }}
    code {{ background: #f6f8fa; padding: .2em .4em; border-radius: 3px; font-size: .9em; }}
    pre code {{ background: none; padding: 0; }}
    table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
    th, td {{ border: 1px solid #d0d7de; padding: .5rem .75rem; text-align: left; }}
    th {{ background: #f6f8fa; font-weight: 600; }}
    tr:nth-child(even) {{ background: #f6f8fa; }}
  </style>
</head>
<body>
{body}
</body>
</html>"""


class ReportRenderer:
    """Renders markdown report content as styled HTML and constructs browser view URLs.

    Inject into Orchestrator and the HTTP route handler. Replace with a Jinja2 or PDF
    subclass to change output format without touching either call site.

    Args:
        base_url: Public base URL of this server (e.g. https://netra.atlasmind.de).
            When None, build_view_url always returns None and view_url is omitted
            from tool responses.
    """

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url.rstrip("/") if base_url else None

    def build_view_url(self, report_id: str) -> str | None:
        """Return the public browser URL for report_id, or None when base_url is unset."""
        if not self._base_url:
            return None
        return f"{self._base_url}/report/{report_id}"

    def render_html(self, report_id: str, content: str) -> str:
        """Render a markdown string as a self-contained HTML page."""
        body = md_lib.markdown(content, extensions=["tables", "fenced_code"])
        return _REPORT_TEMPLATE.format(body=body, report_id=report_id)
