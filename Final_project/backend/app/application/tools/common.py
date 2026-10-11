"""Argument handling shared by the tools (Shared, step 3)."""

import re
import unicodedata
from typing import Any

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
