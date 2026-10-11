"""PDF → text with page offsets (RAG_PLAN §3.7). pypdf only: pure Python, text only, no
JavaScript, forms or attachments. Each page is normalized on its own, so the offsets point
into the final text. Owner: RAG."""

import io
import logging
import re
from collections import Counter

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.application.indexing.normalize import normalize_text
from app.domain.errors import ExtractionError

logger = logging.getLogger("app.pdf")
_HYPHEN = re.compile(r"(\w)-\n(?=[a-z])")


def _clean_page(text: str) -> str:
    return normalize_text(_HYPHEN.sub(r"\1", text))


def _drop_running_lines(pages: list[str]) -> list[str]:
    """Lines that repeat on more than half the pages (headers, footers, page numbers)."""
    if len(pages) < 3:
        return pages
    counts = Counter(
        line.strip() for page in pages for line in {ln.strip() for ln in page.splitlines()}
    )
    running = {
        line for line, n in counts.items() if line and n > len(pages) / 2 and len(line) <= 120
    }
    numbers = re.compile(r"^(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?$", re.IGNORECASE)
    return [
        "\n".join(
            ln
            for ln in page.splitlines()
            if ln.strip() not in running and not numbers.match(ln.strip())
        )
        for page in pages
    ]


class PypdfExtractor:
    def __init__(self, max_pages: int = 150, max_chars: int = 2_000_000) -> None:
        self._max_pages, self._max_chars = max_pages, max_chars

    def pdf_to_text(self, data: bytes) -> tuple[str, tuple[tuple[int, int], ...]]:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise ExtractionError("The PDF is password-protected.")
            count = len(reader.pages)
        except ExtractionError:
            raise
        except (PdfReadError, ValueError, OSError, KeyError) as err:
            raise ExtractionError("The PDF could not be read.") from err
        if count > self._max_pages:
            raise ExtractionError(f"The PDF has {count} pages; the limit is {self._max_pages}.")
        raw: list[str] = []
        failed = 0
        total = 0
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:  # one broken page must not fail the file  # noqa: BLE001
                text, failed = "", failed + 1
            total += len(text)
            if total > self._max_chars:
                raise ExtractionError("The PDF has too much text to index.")
            raw.append(_clean_page(text))
        if count and failed == count:
            raise ExtractionError("No page of the PDF could be read.")
        pages = _drop_running_lines(raw)
        parts: list[str] = []
        offsets: list[tuple[int, int]] = []
        position = 0
        for number, text in enumerate(pages, 1):
            text = normalize_text(text)
            offsets.append((number, position))
            parts.append(text)
            position += len(text) + 2  # "\n\n" between pages
        logger.info("pdf pages=%d failed=%d chars=%d", count, failed, position)
        return "\n\n".join(parts), tuple(offsets)
