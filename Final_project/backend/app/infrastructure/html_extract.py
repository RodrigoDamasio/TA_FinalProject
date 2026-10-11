"""Step 3 STUB of HtmlExtractor: strips tags. SEARXNG_TOOL_PLAN §3.8 replaces it with
trafilatura. Owner: SEARXNG."""

import html
import re

_DROP = re.compile(r"<(script|style|noscript|nav|footer|header)\b.*?</\1>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


class TagStripExtractor:
    def html_to_markdown(self, page_html: str, url: str) -> str:
        text = _TAG.sub("\n", _DROP.sub(" ", page_html))
        lines = (" ".join(line.split()) for line in html.unescape(text).splitlines())
        return "\n\n".join(line for line in lines if line)
