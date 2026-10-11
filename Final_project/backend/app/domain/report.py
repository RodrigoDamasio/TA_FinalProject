"""The structured report (BACKEND_PLAN §5.4): parsed from the final Markdown by
application/report.py. References list only the evidence the report cites."""

from dataclasses import dataclass
from typing import Any

from .depth import Depth
from .evidence import SourceKind


@dataclass(frozen=True)
class ReportSection:
    heading: str
    content: str  # Markdown, keeps [E…] citations


@dataclass(frozen=True)
class Reference:
    id: str  # "E3"
    title: str
    url: str | None
    document_name: str | None
    page: int | None
    source_kind: SourceKind
    snippet: str  # ≤ 300 chars


@dataclass(frozen=True)
class Report:
    title: str
    summary: str
    sections: tuple[ReportSection, ...]
    references: tuple[Reference, ...]  # cited evidence, in order of first citation
    depth: Depth
    word_count: int


def report_to_dict(r: Report) -> dict[str, Any]:
    return {
        "title": r.title,
        "summary": r.summary,
        "sections": [{"heading": s.heading, "content": s.content} for s in r.sections],
        "references": [
            {
                "id": x.id,
                "title": x.title,
                "url": x.url,
                "document_name": x.document_name,
                "page": x.page,
                "source_kind": x.source_kind.value,
                "snippet": x.snippet,
            }
            for x in r.references
        ],
        "depth": r.depth.value,
        "word_count": r.word_count,
    }


def report_from_dict(d: dict[str, Any]) -> Report:
    return Report(
        d["title"],
        d["summary"],
        tuple(ReportSection(s["heading"], s["content"]) for s in d["sections"]),
        tuple(
            Reference(
                x["id"],
                x["title"],
                x["url"],
                x["document_name"],
                x["page"],
                SourceKind(x["source_kind"]),
                x["snippet"],
            )
            for x in d["references"]
        ),
        Depth(d["depth"]),
        d["word_count"],
    )
