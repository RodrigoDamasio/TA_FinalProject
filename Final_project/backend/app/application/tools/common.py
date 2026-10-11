"""Argument and URL helpers shared by the tools (SEARXNG_TOOL_PLAN §3.4)."""

import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_EVIDENCE_ID = re.compile(r"^\[?E?(\d{1,4})\]?$", re.IGNORECASE)


def normalize_query(value: Any) -> str | None:
    """NFKC, whitespace collapsed, trimmed; None unless 2–300 characters."""
    if not isinstance(value, str):
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    return text if 2 <= len(text) <= 300 else None


def parse_evidence_id(value: Any) -> int | None:
    m = _EVIDENCE_ID.match(str(value).strip()) if value is not None else None
    return int(m.group(1)) if m else None


_TRACKING = ("utm_", "gclid", "fbclid", "mc_cid", "mc_eid")
_WIKI_HEADING = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def normalize_url(url: str) -> str:
    """The web source_key: lower-case scheme and host, default ports and fragments dropped,
    tracking parameters removed, trailing '/' stripped except at the root (SEARXNG §3.4)."""
    parts = urlsplit(url.strip())
    scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
    port = parts.port
    netloc = (
        host
        if port is None or (scheme, port) in (("http", 80), ("https", 443))
        else f"{host}:{port}"
    )
    query = "&".join(
        p
        for p in parts.query.split("&")
        if p and not p.split("=", 1)[0].lower().startswith(_TRACKING)
    )
    path = parts.path.rstrip("/") if parts.path not in ("", "/") else "/"
    return urlunsplit((scheme, netloc, path, query, ""))


def wiki_headings_to_markdown(text: str) -> str:
    """MediaWiki plain-text headings (`== History ==`) → Markdown (`## History`)."""
    return _WIKI_HEADING.sub(lambda m: "#" * len(m.group(1)) + " " + m.group(2), text)


def cut_at_paragraph(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n\n", 0, limit)
    return (text[: cut if cut > limit // 2 else limit].rstrip(), True)
