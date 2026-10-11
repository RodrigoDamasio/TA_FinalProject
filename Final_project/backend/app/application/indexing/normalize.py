"""Text normalization before chunking (RAG_PLAN §3.1), so hashes and offsets are stable."""

import re
import unicodedata

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")
_TRAILING = re.compile(r"[ \t]+\n")
_BLANKS = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """NFC, control and bidi-override characters removed (newline and tab kept), CRLF → LF,
    trailing spaces removed, three or more blank lines → two."""
    text = unicodedata.normalize("NFC", text)
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = _TRAILING.sub("\n", text)
    return _BLANKS.sub("\n\n", text).strip()
