"""Final Markdown → structured Report (BACKEND_PLAN §5.4, v1.1 signature). Deterministic and
never raises. Step 3 version: WRITER_PLAN §3.6 refines the fallbacks, the summary cap and the
word count. Owner: WRITER.

Format: `# Title`, summary paragraph(s), `## ` sections citing [E<n>]. A trailing
Sources/References section is dropped: references are rebuilt from the cited evidence."""

import re
from collections.abc import Callable, Mapping

from app.domain.depth import Depth
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.report import Reference, Report, ReportSection

from .citations import CITATION, cited_numbers, normalize_citations

SNIPPET_CHARS = 300
_REFERENCES_HEADING = re.compile(r"^(sources|references|bibliography)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"\b[\w'-]+\b")


def _first_sentence(text: str) -> str:
    plain = " ".join(CITATION.sub("", text).split())
    return _SENTENCE.split(plain, 1)[0] if plain else ""


def _words(text: str) -> int:
    return len(_WORD.findall(CITATION.sub("", text)))


def parse_report(
    markdown: str,
    evidence: list[EvidenceRef],
    *,
    question: str = "",
    depth: Depth = Depth.DETAILED,
    snippets: Mapping[int, str] | None = None,
) -> Report:
    text = normalize_citations(markdown or "").strip()
    title = ""
    summary_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and not title and not sections:
            title = stripped[2:].strip()
        elif stripped.startswith("## "):
            sections.append((stripped[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
        else:
            summary_lines.append(line)
    while sections and _REFERENCES_HEADING.match(sections[-1][0]):
        sections.pop()
    built = tuple(
        ReportSection(heading, "\n".join(body).strip()) for heading, body in sections if heading
    )
    summary = "\n".join(summary_lines).strip()
    if not summary and built:
        summary = _first_sentence(built[0].content)
    title = title or question.strip() or "Research report"

    by_n = {r.n: r for r in evidence}
    body = "\n".join([summary, *(s.content for s in built)])
    snippets = snippets or {}
    references = tuple(
        Reference(
            by_n[n].label,
            by_n[n].title,
            by_n[n].url,
            by_n[n].document_name,
            by_n[n].page,
            by_n[n].source_kind,
            " ".join(snippets.get(n, "").split())[:SNIPPET_CHARS],
        )
        for n in cited_numbers(body)
        if n in by_n
    )
    words = _words(summary) + sum(_words(s.content) for s in built)
    return Report(title, summary, built, references, depth, words)


def reference_label(ref: Reference) -> str:
    """The string form used in ResearchView.report.references (assignment shape)."""
    if ref.source_kind is SourceKind.DOCUMENTS:
        name = ref.document_name or ref.title
        return f"[{ref.id}] {name}, p. {ref.page}" if ref.page else f"[{ref.id}] {name}"
    return f"[{ref.id}] {ref.title} — {ref.url}" if ref.url else f"[{ref.id}] {ref.title}"


def snippets_for(
    evidence: list[EvidenceRef],
    numbers: list[int],
    get: Callable[[list[str]], list[EvidenceChunk]],
) -> dict[int, str]:
    """Load the text of the cited evidence (through Retriever.get) for reference snippets."""
    by_n = {r.n: r for r in evidence}
    wanted = [by_n[n] for n in numbers if n in by_n]
    texts = {c.id: c.text for c in get([r.chunk_id for r in wanted])} if wanted else {}
    return {r.n: texts[r.chunk_id][:SNIPPET_CHARS] for r in wanted if r.chunk_id in texts}
