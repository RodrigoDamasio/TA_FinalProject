"""Chunking (RAG_PLAN §3.2): pure functions from a normalized text to drafts in CHARACTER
offsets, so PDF page numbers stay exact.

- a text that fits the budget is one chunk (a search snippet)
- Markdown (ATX `#` or wiki `== H ==` headings outside code fences): one draft per heading
  section with its heading path; small sections merge forward; big ones are split in windows
- prose (plain text, PDF pages): paragraph → sentence → word windows, packed greedily, with
  an overlap of the last units of the previous window
- PDF: windows per page; a chunk never crosses a page; short pages join the next one

M4's Markdown chunker is the base (heading paths, merge_small), moved from lines to
character offsets. Owner: RAG."""

import re
from dataclasses import dataclass

from app.domain.evidence import EvidenceChunk

CHUNKER_VERSION = "1"
_ATX = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_WIKI = re.compile(r"^(={2,6})[ \t]*(.+?)[ \t]*\1[ \t]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_PARAGRAPH = re.compile(r"\n[ \t]*\n")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class ChunkOptions:
    max_chars: int = 1400
    min_chars: int = 400
    overlap: float = 0.15


@dataclass(frozen=True)
class Draft:
    start: int
    end: int
    heading: str | None = None
    page: int | None = None


def header(title: str, heading: str | None) -> str:
    return title if not heading else f"{title} · {heading}"


def embed_text(c: EvidenceChunk) -> str:
    """What is embedded and keyword-indexed: the header line, then the body. Rebuilt the
    same way from stored metadata, so BM25 rebuilds match indexing exactly."""
    return f"{header(c.title, c.heading)}\n{c.text}"


# ---- units and windows ----------------------------------------------------------------------


def _spans(text: str, pattern: re.Pattern[str], start: int, end: int) -> list[tuple[int, int]]:
    out, pos = [], start
    for m in pattern.finditer(text, start, end):
        if m.start() > pos:
            out.append((pos, m.start()))
        pos = m.end()
    if pos < end:
        out.append((pos, end))
    return [(s, e) for s, e in out if text[s:e].strip()]


def _units(text: str, start: int, end: int, budget: int) -> list[tuple[int, int]]:
    """Paragraphs; a paragraph over the budget → sentences; a sentence over it → cut on spaces."""
    units: list[tuple[int, int]] = []
    for ps, pe in _spans(text, _PARAGRAPH, start, end):
        if pe - ps <= budget:
            units.append((ps, pe))
            continue
        for ss, se in _spans(text, _SENTENCE, ps, pe):
            while se - ss > budget:
                cut = text.rfind(" ", ss, ss + budget)
                cut = cut if cut > ss else ss + budget
                units.append((ss, cut))
                ss = cut
                while ss < se and text[ss] == " ":
                    ss += 1
            if se > ss:
                units.append((ss, se))
    return units


def windows(text: str, start: int, end: int, budget: int, overlap: float) -> list[tuple[int, int]]:
    """Greedy packing of units up to `budget`; each new window starts with the last units of
    the previous one, up to `overlap` × budget characters."""
    units = _units(text, start, end, budget)
    out: list[tuple[int, int]] = []
    i = 0
    while i < len(units):
        j = i
        while j + 1 < len(units) and units[j + 1][1] - units[i][0] <= budget:
            j += 1
        out.append((units[i][0], units[j][1]))
        if j + 1 >= len(units):
            break
        nxt = j + 1
        back = nxt
        while back - 1 > i and units[nxt - 1][1] - units[back - 1][0] <= overlap * budget:
            back -= 1
        if units[nxt][1] - units[back][0] > budget:
            back = nxt
        i = back
    return out


# ---- Markdown -----------------------------------------------------------------------------------


def _headings(text: str) -> list[tuple[int, int, str]]:
    """(offset, level, title) of every heading outside code fences."""
    found, in_fence, pos = [], False, 0
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence:
            m = _ATX.match(line)
            if m:
                found.append((pos, len(m.group(1)), m.group(2).strip()))
            else:
                w = _WIKI.match(line)
                if w:
                    found.append((pos, len(w.group(1)), w.group(2).strip()))  # == H == is level 2
        pos += len(line) + 1
    return found


def is_markdown(text: str) -> bool:
    return bool(_headings(text))


def sections(text: str) -> list[Draft]:
    """One draft per heading section, with its heading path ("Guide > Setup")."""
    found = _headings(text)
    out: list[Draft] = []
    if not found or found[0][0] > 0:
        first_end = found[0][0] if found else len(text)
        if text[:first_end].strip():
            out.append(Draft(0, first_end, None))
    stack: list[tuple[int, str]] = []
    for i, (offset, level, title) in enumerate(found):
        stack = [(lv, t) for lv, t in stack if lv < level] + [(level, title)]
        end = found[i + 1][0] if i + 1 < len(found) else len(text)
        out.append(Draft(offset, end, " > ".join(t for _, t in stack)))
    return out


def merge_small(drafts: list[Draft], min_chars: int) -> list[Draft]:
    """A section under min_chars merges forward into the next one (the merged chunk takes the
    next section's heading, as in M4)."""
    merged: list[Draft] = []
    carry: Draft | None = None
    for i, d in enumerate(drafts):
        start = carry.start if carry else d.start
        heading = d.heading or (carry.heading if carry else None)
        if d.end - start < min_chars and i + 1 < len(drafts):
            carry = Draft(start, d.end, heading)
            continue
        merged.append(Draft(start, d.end, heading))
        carry = None
    return merged


# ---- entry point ---------------------------------------------------------------------------------


def chunk(
    text: str,
    title: str,
    opts: ChunkOptions,
    pages: tuple[tuple[int, int], ...] = (),
) -> list[Draft]:
    """Drafts for one normalized source text (offsets into `text`)."""
    if not text.strip():
        return []
    if pages:
        return _pdf(text, title, opts, pages)
    budget = max(200, opts.max_chars - len(title) - 1)
    if len(text) <= budget:
        return [Draft(0, len(text), None)]
    if is_markdown(text):
        out: list[Draft] = []
        for sec in merge_small(sections(text), opts.min_chars):
            b = max(200, opts.max_chars - len(header(title, sec.heading)) - 1)
            if sec.end - sec.start <= b:
                out.append(sec)
            else:
                out += [
                    Draft(s, e, sec.heading)
                    for s, e in windows(text, sec.start, sec.end, b, opts.overlap)
                ]
        return [d for d in out if text[d.start : d.end].strip()]
    return [Draft(s, e) for s, e in windows(text, 0, len(text), budget, opts.overlap)]


def _pdf(
    text: str, title: str, opts: ChunkOptions, pages: tuple[tuple[int, int], ...]
) -> list[Draft]:
    budget = max(200, opts.max_chars - len(title) - 1)
    bounds = [
        (number, start, pages[i + 1][1] if i + 1 < len(pages) else len(text))
        for i, (number, start) in enumerate(pages)
    ]
    groups: list[tuple[int, int, int]] = []  # (first page, start, end)
    pending: tuple[int, int] | None = None
    for number, start, end in bounds:
        if not text[start:end].strip():
            continue  # blank page
        first, s = pending if pending else (number, start)
        if end - s < opts.min_chars and number != bounds[-1][0]:
            pending = (first, s)
            continue
        groups.append((first, s, end))
        pending = None
    if pending:
        groups.append((pending[0], pending[1], len(text)))
    starts = [(start, number) for number, start, _ in bounds]

    def page_at(offset: int) -> int:
        return max((n for s, n in starts if s <= offset), default=starts[0][1])

    out: list[Draft] = []
    for first, start, end in groups:
        for i, (s, e) in enumerate(windows(text, start, end, budget, opts.overlap)):
            out.append(Draft(s, e, None, first if i == 0 else page_at(s)))
    return out
