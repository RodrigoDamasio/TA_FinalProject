"""The tool contract (BACKEND_PLAN §10.2, v1.1 §24) and what every tool shares: the run's
ToolBox, the evidence pipeline (index → retrieve → number) and the ONE renderer that gives
every tool result the same layout (RESEARCHER_PLAN §3.6).

App-written lines start with an upper-case keyword and hold no untrusted text. Titles,
snippets and page text only appear inside DataFence blocks."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

from app.application.security import DataFence
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.ports import Indexer, Retriever, SourceText, ToolSpec
from app.domain.transcript import ToolCallRecord

_TERM = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset(
    "the and for with from that this what which how why when are was were has have does into "
    "about over under between versus than then also more most less vs".split()
)


@dataclass(frozen=True)
class ToolOutput:
    content: str  # what the model receives, already fenced by the tool
    record: ToolCallRecord  # what the transcript and SSE show


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    run_scope: str  # "run:<id>"
    read_scopes: list[str]  # e.g. ["ws:<id>", "demo"] for search_documents
    add_evidence: Callable[[list[EvidenceChunk]], list[EvidenceRef]]  # numbers chunks in the run
    evidence_by_n: Callable[[int], EvidenceRef | None]  # for read_result
    fence: DataFence
    sources: frozenset[SourceKind] = frozenset()  # v1.1: the run's allowed sources
    evidence_count: Callable[[], int] = lambda: 0  # additive: numbers issued so far
    # v1.2: run scopes of earlier turns of THIS conversation (newest first), computed by the
    # Supervisor from stored runs; search_conversation reads nothing else
    conversation_scopes: tuple[str, ...] = ()


class Tool(Protocol):
    name: str
    family: SourceKind  # which `sources` value enables it
    spec: ToolSpec

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput: ...


ToolMemo = dict[tuple[str, str], ToolOutput]  # (tool, normalized args) → output; run-wide


@dataclass
class ToolBox:
    """The tools ONE worker step may call. Built per step by ToolRegistry.box (v1.1)."""

    tools: dict[str, Tool]
    ctx: ToolContext
    sources: frozenset[SourceKind]
    memo: ToolMemo = field(default_factory=dict)

    def _allowed(self) -> list[Tool]:
        return [t for t in self.tools.values() if t.family in self.sources]

    def offered(self) -> list[ToolSpec]:
        return [t.spec for t in self._allowed()]

    def call(self, name: str, args: dict[str, Any]) -> ToolOutput:
        tool = self.tools.get(name)
        if tool is None or tool.family not in self.sources:
            available = [t.name for t in self._allowed()]
            record = ToolCallRecord(
                "researcher", name, _short(args), "refused", error="tool not available"
            )
            text = f"REFUSED Tool {name} is not available in this run. "
            text += f"Available: {', '.join(available) or 'none'}."
            return ToolOutput(text, record)
        return tool.run(args, self.ctx)


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {t.name: t for t in tools}

    def box(self, ctx: ToolContext, sources: frozenset[SourceKind], memo: ToolMemo) -> ToolBox:
        return ToolBox(self._tools, replace(ctx, sources=sources), sources, memo)

    def specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "family": t.family.value,
                "description": t.spec.description,
                "parameters": t.spec.parameters,
            }
            for t in self._tools.values()
        ]


# ---- the evidence pipeline every tool uses -------------------------------------------------


@dataclass(frozen=True)
class Gathered:
    blocks: list[tuple[EvidenceRef, EvidenceChunk]]  # numbered, in retrieval order
    new: int  # how many numbers are new to the run


def gather(
    ctx: ToolContext,
    indexer: Indexer,
    retriever: Retriever,
    texts: list[SourceText],
    query: str,
    k: int,
) -> Gathered:
    """Index `texts` in the run scope, retrieve the best `k` chunks of THESE sources for the
    query, then number them in the run (a known chunk keeps its number)."""
    if not texts:
        return Gathered([], 0)
    indexer.index(texts)
    keys = sorted({t.source_key for t in texts})
    hits = retriever.search(query, [ctx.run_scope], k, source_keys=keys)
    return number(ctx, [h.chunk for h in hits])


def number(ctx: ToolContext, chunks: list[EvidenceChunk]) -> Gathered:
    if not chunks:
        return Gathered([], 0)
    before = ctx.evidence_count()
    refs = ctx.add_evidence(chunks)
    new = len({r.n for r in refs if r.n > before})
    return Gathered(list(zip(refs, chunks, strict=True)), new)


# ---- rendering (RESEARCHER_PLAN §3.6) ----------------------------------------------------------


def evidence_range(ns: list[int] | tuple[int, ...]) -> str:
    if not ns:
        return "none"
    ordered = list(ns)
    if ordered == list(range(ordered[0], ordered[0] + len(ordered))) and len(ordered) > 1:
        return f"E{ordered[0]}-E{ordered[-1]}"
    return ",".join(f"E{n}" for n in ordered)


def where(chunk: EvidenceChunk) -> str:
    if chunk.source_kind is SourceKind.WIKIPEDIA:
        return "wikipedia"
    if chunk.source_kind is SourceKind.DOCUMENTS:
        name = chunk.document_name or chunk.title
        return f"{name} p. {chunk.page}" if chunk.page else name
    host = urlsplit(chunk.url or "").hostname or ""
    return host.removeprefix("www.")


def content_terms(query: str) -> list[str]:
    return [t for t in _TERM.findall(query.lower()) if t not in _STOP]


def is_weak(query: str, chunks: list[EvidenceChunk]) -> bool:
    """Fewer than 2 evidence blocks, or fewer than half the query's content terms appear."""
    if len(chunks) < 2:
        return True
    terms = content_terms(query)
    if not terms:
        return False
    text = " ".join(f"{c.title} {c.text}" for c in chunks).lower()
    return sum(1 for t in terms if t in text) * 2 < len(terms)


def cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " …"


def render_tool_result(
    record: ToolCallRecord,
    blocks: list[tuple[EvidenceRef, EvidenceChunk]],
    fence: DataFence,
    *,
    new: int = 0,
    note: str | None = None,
    snippet_chars: int = 900,
) -> str:
    """The one layout every tool result uses (v1.1: shared by SEARXNG, RAG and RESEARCHER)."""
    args = " ".join(f'{k}="{_quote(v)}"' for k, v in record.args.items())
    head = f"RESULT {record.tool} {args} provider={record.provider or 'none'}"
    head += f" results={record.results} evidence={evidence_range(record.evidence_ns)} new={new}"
    lines = [head]
    if note:
        lines.append(f"NOTE {note}")
    if record.status == "empty":
        lines.append(
            "HINT No results. Rewrite the query (fewer or different terms) or try another tool."
        )
    elif record.status == "error":
        lines.append(
            f"ERROR {record.error or 'the tool failed'}. Try another tool or write findings."
        )
    elif record.status == "refused":
        lines.append(f"REFUSED {record.error or 'not allowed'}.")
    elif record.weak:
        lines.append(
            "HINT Few results match the query terms. Consider a more specific query, synonyms, "
            "the official name, or another tool."
        )
    for ref, chunk in blocks:
        lines.append(
            fence.wrap(
                cut(chunk.text, snippet_chars),
                "evidence",
                ref=ref.label,
                source=chunk.source_kind.value,
                title=chunk.title,
                where=where(chunk),
            )
        )
    return "\n".join(lines)


def _quote(value: object) -> str:
    return str(value).replace('"', "'").replace("\n", " ")[:300]


def _short(args: dict[str, Any]) -> dict[str, Any]:
    return {str(k)[:40]: str(v)[:300] for k, v in list(args.items())[:4]}
