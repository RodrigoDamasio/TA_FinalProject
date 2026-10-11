"""Final Markdown → structured Report (BACKEND_PLAN §5.4, v1.1 signature; WRITER_PLAN §3.6).
Pure, deterministic and never raises. Owner: WRITER.

Format: `# Title`, summary paragraph(s), `## ` sections citing [E<n>]. A trailing
Sources/References section is dropped: references are rebuilt from the cited evidence.
Fallbacks: a bold first line or the question for the title; a "Summary" section or the first
sentence of the first section for the summary; `###` sections when there is no `##`; one
`Details` section when there are no headings at all."""

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.domain.depth import Depth
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.report import Reference, Report, ReportSection

from .citations import CITATION, clean_citations

logger = logging.getLogger(__name__)

SNIPPET_CHARS = 300
TITLE_CHARS = 200
SUMMARY_CHARS = 1200
DEFAULT_TITLE = "Research report"
UNTITLED = "Untitled source"

# ---- patterns ---------------------------------------------------------------------------------

_LEAKED_FENCE = re.compile(r'^\s*</?data\s+id="[^"]*"[^>]*>\s*$', re.IGNORECASE)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_OUTER_FENCE = re.compile(r"\A(`{3,}|~{3,})[ \t]*(?:markdown|md)?[ \t]*\n(.*)\n\1[ \t]*\Z", re.S)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})[ \t]+(.*?)[ \t]*$")
_BOLD_LINE = re.compile(r"^\s*(\*\*|__)(?P<t>[^*_\n]+?)\1\s*$")
_IMAGE = re.compile(r"!\[[^\]\n]*\]\([^)\n]*\)")
_LINK = re.compile(r"\[(?P<text>[^\]\n]*)\]\((?P<url>[^)\s]+)(?:\s+\"[^\"\n]*\")?\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_EMPHASIS = re.compile(r"[*_`]+")
_NUMBERING = re.compile(r"^(?:\d+(?:\.\d+)*|[IVXLCM]+)[.)]\s+")
_SOURCES = re.compile(
    r"^(?:sources|references|bibliography|works cited|citations|further reading)$", re.I
)
_SOURCES_LINE = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:\*\*|__)?\s*(?:sources|references|bibliography|works cited|citations|"
    r"further reading)\s*:?\s*(?:\*\*|__)?\s*:?\s*$",
    re.IGNORECASE,
)
_SUMMARY = re.compile(
    r"^(?:summary|executive summary|overview|tl;?dr|abstract|key takeaways)$", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_WORD = re.compile(r"\w+(?:['’-]\w+)*")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
_BULLET = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+")
_BLANKS = re.compile(r"\n{3,}")


# ---- step 1: normalization ----------------------------------------------------------------------


def unwrap_fence(text: str) -> str:
    """Remove a code fence around the whole document (```markdown … ```)."""
    m = _OUTER_FENCE.match(text.strip())
    return m.group(2).strip() if m else text.strip()


def _normalize(markdown: str | None) -> str:
    text = (markdown or "").replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    text = unwrap_fence(text)
    lines = [line.rstrip() for line in text.split("\n") if not _LEAKED_FENCE.match(line)]
    return "\n".join(lines).strip()


# ---- step 2: lines and code blocks ------------------------------------------------------------


def _code_mask(lines: list[str]) -> list[bool]:
    """True for each line inside (or opening/closing) a fenced code block."""
    mask, opener = [], ""
    for line in lines:
        m = _FENCE.match(line)
        if opener:
            mask.append(True)
            if m and m.group(1)[0] == opener[0] and len(m.group(1)) >= len(opener):
                if not line.strip()[len(m.group(1)) :].strip():
                    opener = ""
        elif m:
            mask.append(True)
            opener = m.group(1)
        else:
            mask.append(False)
    return mask


# ---- step 4: links and images -------------------------------------------------------------------


def _clean_links(text: str, evidence_urls: set[str]) -> str:
    text = _IMAGE.sub("", text)
    return _LINK.sub(
        lambda m: m.group(0) if m.group("url") in evidence_urls else m.group("text"), text
    )


# ---- titles, headings, summary ------------------------------------------------------------------


def _plain_heading(text: str) -> str:
    text = re.sub(r"\s#+$", "", text.strip())
    text = CITATION.sub("", text)
    text = _EMPHASIS.sub("", text)
    return " ".join(text.split())


def _cap(text: str, limit: int) -> str:
    """Cut at a word boundary, at most `limit` characters."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    space = head.rfind(" ")
    return (head[:space] if space > limit // 2 else head).rstrip()


def _section_heading(text: str, k: int) -> str:
    heading = _NUMBERING.sub("", _plain_heading(text)).rstrip(":").strip()
    return _cap(heading, TITLE_CHARS) or f"Section {k}"


def _first_sentence(content: str) -> str:
    paragraph = next((p for p in content.split("\n\n") if p.strip()), "")
    lines = [_BULLET.sub("", line).lstrip("#> ").strip() for line in paragraph.split("\n")]
    plain = " ".join(" ".join(lines).split())
    m = _SENTENCE_END.search(plain)
    return plain[: m.end()] if m else plain


def _cap_summary(summary: str) -> str:
    if len(summary) <= SUMMARY_CHARS:
        return summary
    ends = [m.end() for m in _SENTENCE_END.finditer(summary[:SUMMARY_CHARS])]
    if ends:
        return summary[: ends[-1]].strip()
    return _cap(summary, SUMMARY_CHARS - 1) + "…"


def _join(*blocks: str) -> str:
    return _BLANKS.sub("\n\n", "\n\n".join(b.strip() for b in blocks if b.strip()))


def _paragraphs(lines: list[str], mask: list[bool]) -> list[str]:
    """Blank-line separated paragraphs; a code block stays inside one paragraph."""
    out: list[list[str]] = [[]]
    for line, code in zip(lines, mask, strict=True):
        if not line.strip() and not code:
            if out[-1]:
                out.append([])
        else:
            out[-1].append(line)
    return ["\n".join(p).strip() for p in out if p]


def _cut_sources(content: str) -> str:
    """Inside the last section: a `### Sources`, `**Sources**` or `Sources:` line cuts the rest."""
    lines = content.split("\n")
    for i, (line, code) in enumerate(zip(lines, _code_mask(lines), strict=True)):
        if not code and _SOURCES_LINE.match(line):
            return "\n".join(lines[:i]).strip()
    return content


# ---- step 9: word count -------------------------------------------------------------------------


def count_words(text: str) -> int:
    """Words of report prose: citations, URLs and Markdown markers are not words."""
    text = CITATION.sub(" ", text)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(lambda m: m.group("text"), text)
    text = _URL.sub(" ", text)
    kept = []
    for line in text.split("\n"):
        if _FENCE.match(line) or _TABLE_RULE.match(line):
            continue
        kept.append(_BULLET.sub(" ", line))
    text = re.sub(r"[#*_`|>]", " ", "\n".join(kept))
    return len(_WORD.findall(text))


# ---- step 10: references ------------------------------------------------------------------------


def format_snippet(text: str) -> str:
    """One line, at most SNIPPET_CHARS characters, cut at a word boundary with '…'."""
    flat = " ".join(text.split())
    if len(flat) <= SNIPPET_CHARS:
        return flat
    head = flat[:SNIPPET_CHARS]
    space = head.rfind(" ", 0, SNIPPET_CHARS - 1)
    return head[:space].rstrip() + "…" if space > SNIPPET_CHARS // 2 else head


def _host(url: str | None) -> str:
    if not url:
        return ""
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _reference(ref: EvidenceRef, snippets: Mapping[int, str]) -> Reference:
    title = ref.title.strip() or _host(ref.url) or (ref.document_name or "").strip() or UNTITLED
    return Reference(
        ref.label,
        title,
        ref.url,
        ref.document_name,
        ref.page,
        ref.source_kind,
        format_snippet(snippets.get(ref.n, "")),
    )


def _references(
    texts: list[str], by_n: dict[int, EvidenceRef], snippets: Mapping[int, str]
) -> tuple[Reference, ...]:
    seen: list[int] = []
    for text in texts:
        for n in (int(x) for x in CITATION.findall(text)):
            if n in by_n and n not in seen:
                seen.append(n)
    return tuple(_reference(by_n[n], snippets) for n in seen)


# ---- the parser ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Title:
    text: str  # "" when the draft has none
    line: int  # index of the title line, -1 when none


def _find_title(lines: list[str], mask: list[bool]) -> _Title:
    """The first `# ` heading outside code, unless a section heading comes before it; else a
    lone bold first line."""
    for i, (line, code) in enumerate(zip(lines, mask, strict=True)):
        if code:
            continue
        m = _HEADING.match(line)
        if m is None:
            continue
        if len(m.group(1)) == 1 and (text := _plain_heading(m.group(2))):
            return _Title(_cap(text, TITLE_CHARS), i)
        break  # a section heading before any title: no title
    first = next((i for i, line in enumerate(lines) if line.strip()), None)
    if first is not None and not mask[first] and (b := _BOLD_LINE.match(lines[first])):
        text = _plain_heading(b.group("t"))
        if text:
            return _Title(_cap(text, TITLE_CHARS), first)
    return _Title("", -1)


def has_title(markdown: str | None) -> bool:
    """True when the draft carries its own title (`# ` heading or a bold first line)."""
    try:
        lines = _normalize(markdown).split("\n")
        return bool(_find_title(lines, _code_mask(lines)).text)
    except Exception:
        return False


def _fallback_title(question: str) -> str:
    return _cap(" ".join(question.split()), TITLE_CHARS) or DEFAULT_TITLE


def _parse(
    markdown: str | None,
    evidence: list[EvidenceRef],
    question: str,
    depth: Depth,
    snippets: Mapping[int, str],
) -> Report:
    by_n = {r.n: r for r in evidence}
    text = _normalize(markdown)
    text, _, _ = clean_citations(text, set(by_n))
    text = _clean_links(text, {r.url for r in evidence if r.url})
    lines = text.split("\n") if text else []
    mask = _code_mask(lines)

    found = _find_title(lines, mask)
    title = found.text or _fallback_title(question)
    preamble = "\n".join(lines[: max(found.line, 0)]).strip()
    body_lines = lines[found.line + 1 :]
    body_mask = mask[found.line + 1 :]

    levels = {
        len(m.group(1))
        for line, code in zip(body_lines, body_mask, strict=True)
        if not code and (m := _HEADING.match(line))
    }
    level = 2 if 2 in levels else 3 if 3 in levels else 0
    marks = {1, level} if level else {1}

    lead: list[str] = []
    raw: list[tuple[str, list[str]]] = []
    for line, code in zip(body_lines, body_mask, strict=True):
        m = None if code else _HEADING.match(line)
        if m and len(m.group(1)) in marks:
            raw.append((m.group(2), []))
        elif raw:
            raw[-1][1].append(line)
        else:
            lead.append(line)

    if not raw:  # no section headings: first paragraph = summary, the rest = Details
        paragraphs = _paragraphs(lead, body_mask[: len(lead)])
        summary = _join(preamble, paragraphs[0] if paragraphs else "")
        rest = _join(*paragraphs[1:])
        sections = [ReportSection("Details", rest)] if rest else []
    else:
        sections = [
            ReportSection(_section_heading(heading, k), "\n".join(body).strip())
            for k, (heading, body) in enumerate(raw, 1)
        ]
        sections = [s for s in sections if s.content]
        while sections and _SOURCES.match(sections[-1].heading):
            sections.pop()
        if sections:
            last = sections[-1]
            sections[-1] = ReportSection(last.heading, _cut_sources(last.content))
            if not sections[-1].content:
                sections.pop()
        summary = _join(preamble, "\n".join(lead))
        if not summary and sections:
            if _SUMMARY.match(sections[0].heading):
                summary = sections.pop(0).content
            else:
                summary = _first_sentence(sections[0].content)
    summary = _cap_summary(_BLANKS.sub("\n\n", summary))

    contents = [summary, *(s.content for s in sections)]
    words = sum(count_words(c) for c in contents)
    return Report(
        title, summary, tuple(sections), _references(contents, by_n, snippets), depth, words
    )


def _minimal(
    markdown: str | None,
    evidence: list[EvidenceRef],
    question: str,
    depth: Depth,
    snippets: Mapping[int, str],
) -> Report:
    """Safety net (step 11): the whole cleaned text as one section."""
    try:
        text = _normalize(markdown)
    except Exception:
        text = markdown if isinstance(markdown, str) else ""
    by_n = {r.n: r for r in evidence}
    try:
        text, _, _ = clean_citations(text, set(by_n))
    except Exception:
        text = CITATION.sub("", text)
    sections = (ReportSection("Report", text),) if text else ()
    return Report(
        _fallback_title(question),
        _cap(text, 300),
        sections,
        _references([text], by_n, snippets),
        depth,
        count_words(text),
    )


def parse_report(
    markdown: str,
    evidence: list[EvidenceRef],
    *,
    question: str = "",
    depth: Depth = Depth.DETAILED,
    snippets: Mapping[int, str] | None = None,
) -> Report:
    question = question if isinstance(question, str) else ""
    snippets = snippets or {}
    try:
        return _parse(markdown, evidence, question, depth, snippets)
    except Exception as err:  # the contract: never raises
        logger.warning("parse_report fell back to a minimal report: %s", type(err).__name__)
        return _minimal(markdown, evidence, question, depth, snippets)


# ---- labels and snippets ------------------------------------------------------------------------


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0].strip().casefold()


def reference_label(ref: Reference) -> str:
    """The string form used in ResearchView.report.references (assignment shape)."""
    if ref.source_kind is SourceKind.DOCUMENTS:
        name = ref.document_name or ref.title
        label = f"[{ref.id}] {name}" + (f", p. {ref.page}" if ref.page else "")
        if (
            ref.document_name
            and ref.title
            and _stem(ref.title)
            not in (
                _stem(ref.document_name),
                ref.document_name.casefold(),
            )
        ):
            label += f" — {ref.title}"
        return label
    title = f"{ref.title} (Wikipedia)" if ref.source_kind is SourceKind.WIKIPEDIA else ref.title
    return f"[{ref.id}] {title} — {ref.url}" if ref.url else f"[{ref.id}] {title}"


def snippets_for(
    evidence: list[EvidenceRef],
    numbers: list[int],
    get: Callable[[list[str]], list[EvidenceChunk]],
) -> dict[int, str]:
    """Load the text of the cited evidence (one Retriever.get call) for reference snippets.
    Any error gives {}: references then have empty snippets, never a failed run."""
    try:
        by_n = {r.n: r for r in evidence}
        wanted = [by_n[n] for n in dict.fromkeys(numbers) if n in by_n]
        if not wanted:
            return {}
        texts = {c.id: c.text for c in get([r.chunk_id for r in wanted])}
        return {r.n: format_snippet(texts[r.chunk_id]) for r in wanted if r.chunk_id in texts}
    except Exception as err:
        logger.warning("snippets_for failed: %s", type(err).__name__)
        return {}
