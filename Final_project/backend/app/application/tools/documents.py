"""search_documents (step 3 version): searches the workspace + demo library, then copies the
hits into the run scope (Indexer.adopt, v1.1) so the Fact-Checker finds them and they outlive
the workspace. RAG_PLAN §3.5 refines it. Owner: RAG."""

import time
from typing import Any

from app.domain.evidence import SourceKind
from app.domain.ports import Indexer, Retriever, ToolSpec
from app.domain.transcript import ToolCallRecord

from .base import ToolContext, ToolOutput, is_weak, number, render_tool_result
from .common import normalize_query

SPEC = ToolSpec(
    "search_documents",
    "Search the user's uploaded documents and the demo library. Best when the question is "
    "about the user's files or the topics they cover.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "3-8 specific search terms"}},
        "required": ["query"],
    },
)


class SearchDocumentsTool:
    name = "search_documents"
    family = SourceKind.DOCUMENTS
    spec = SPEC

    def __init__(
        self, indexer: Indexer, retriever: Retriever, *, top_k: int = 6, snippet_chars: int = 900
    ) -> None:
        self._indexer, self._retriever = indexer, retriever
        self._k, self._snippet = top_k, snippet_chars

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        started = time.perf_counter()
        query = normalize_query(args.get("query"))
        hits = (
            self._retriever.search(query, ctx.read_scopes, self._k)
            if query and ctx.read_scopes
            else []
        )
        copies = self._indexer.adopt([h.chunk for h in hits], ctx.run_scope) if hits else []
        gathered = number(ctx, copies)
        status = "error" if query is None else ("ok" if hits else "empty")
        record = ToolCallRecord(
            "researcher",
            self.name,
            {"query": query or str(args.get("query"))[:300]},
            status,  # type: ignore[arg-type]
            provider="documents",
            results=len(hits),
            evidence_ns=tuple(r.n for r, _ in gathered.blocks),
            error="query must be 2-300 characters" if query is None else None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            weak=bool(hits) and is_weak(query or "", copies),
        )
        content = render_tool_result(
            record, gathered.blocks, ctx.fence, new=gathered.new, snippet_chars=self._snippet
        )
        return ToolOutput(content, record)
