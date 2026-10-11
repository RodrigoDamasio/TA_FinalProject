"""search_wikipedia (SEARXNG_TOOL_PLAN §3.5): Module 5's Wikipedia client, one page at a time
(so each extract keeps its page id as `wikipedia:<id>`), MediaWiki headings rewritten to
Markdown for the chunker, then index → retrieve → number like every tool. Owner: SEARXNG."""

import time
from typing import Any

from app.domain.errors import KnowledgeUnavailable
from app.domain.evidence import SourceKind
from app.domain.ports import Indexer, KnowledgeSource, Retriever, SourceText, ToolSpec
from app.domain.transcript import ToolCallRecord

from .base import ToolContext, ToolOutput, gather, is_weak, render_tool_result
from .common import normalize_query, wiki_headings_to_markdown

SPEC = ToolSpec(
    "search_wikipedia",
    "Search Wikipedia. Best for established concepts, definitions and history. Returns "
    "numbered evidence.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "3-8 specific search terms"}},
        "required": ["query"],
    },
)


class SearchWikipediaTool:
    name = "search_wikipedia"
    family = SourceKind.WIKIPEDIA
    spec = SPEC

    def __init__(
        self,
        knowledge: KnowledgeSource,
        indexer: Indexer,
        retriever: Retriever,
        *,
        max_pages: int = 3,
        max_chars: int = 6000,
        top_k: int = 6,
        snippet_chars: int = 900,
    ) -> None:
        self._knowledge, self._indexer, self._retriever = knowledge, indexer, retriever
        self._max_pages, self._max_chars = max_pages, max_chars
        self._k, self._snippet = top_k, snippet_chars

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        started = time.perf_counter()
        query = normalize_query(args.get("query"))
        if query is None:
            record = ToolCallRecord(
                "researcher", self.name, {"query": str(args.get("query"))[:300]}, "error",
                error="query must be 2-300 characters",
            )  # fmt: skip
            return ToolOutput(render_tool_result(record, [], ctx.fence), record)
        return self.search(query, ctx, started)

    def search(
        self,
        query: str,
        ctx: ToolContext,
        started: float,
        *,
        tool: str | None = None,
        provider: str = "wikipedia",
        fallback: str | None = None,
        note: str | None = None,
    ) -> ToolOutput:
        """Also used by search_web's fallback, which reports itself as tool=search_web,
        provider=searxng, fallback=wikipedia."""

        def done(status, results=0, gathered=None, error=None):  # noqa: ANN001, ANN202
            blocks = gathered.blocks if gathered else []
            record = ToolCallRecord(
                "researcher",
                tool or self.name,
                {"query": query},
                status,
                provider=provider,
                fallback=fallback,
                results=results,
                evidence_ns=tuple(r.n for r, _ in blocks),
                error=error,
                duration_ms=int((time.perf_counter() - started) * 1000),
                weak=status == "ok" and is_weak(query, [c for _, c in blocks]),
            )
            content = render_tool_result(
                record,
                blocks,
                ctx.fence,
                new=gathered.new if gathered else 0,
                note=note,
                snippet_chars=self._snippet,
            )
            return ToolOutput(content, record)

        try:
            hits = self._knowledge.search(query, self._max_pages)
            pages = [(h, self._knowledge.fetch([h], self._max_chars)) for h in hits]
        except KnowledgeUnavailable:
            error = "web search and wikipedia unavailable" if fallback else "wikipedia unavailable"
            return done("error", error=error)
        texts = [
            SourceText(
                ctx.run_scope,
                SourceKind.WIKIPEDIA,
                f"wikipedia:{hit.page_id}",
                d[0].title,
                wiki_headings_to_markdown(d[0].extract),
                url=d[0].url,
            )  # fmt: skip
            for hit, d in pages
            if d and d[0].extract.strip()
        ]
        if not texts:
            return done("empty")
        gathered = gather(ctx, self._indexer, self._retriever, texts, query, self._k)
        return done("ok", len(texts), gathered)
