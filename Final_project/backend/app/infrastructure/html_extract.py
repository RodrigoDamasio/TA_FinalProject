"""HTML → Markdown with trafilatura (SEARXNG_TOOL_PLAN §3.8): main content only (navigation,
footers, comments and scripts dropped), tables kept, favoring precision. Input is a page
already fetched through the SSRF guard; `trafilatura.fetch_url` is never used (an
architecture test bans it). Nothing is executed or rendered. Owner: SEARXNG."""

import trafilatura


class TrafilaturaExtractor:
    def html_to_markdown(self, html: str, url: str) -> str:
        if not html.strip():
            return ""
        text = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_links=False,
            include_images=False,
            include_comments=False,
            include_tables=True,
            favor_precision=True,
            deduplicate=True,
        )
        return (text or "").strip()
