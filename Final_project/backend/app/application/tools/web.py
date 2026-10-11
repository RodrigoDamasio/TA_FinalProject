"""search_web and read_result (SEARXNG_TOOL_PLAN §3.3-§3.6). Owner: SEARXNG.

Fallback rule (v1.1 §24): only when SearXNG FAILS and `wikipedia` is an allowed source.
Empty results are reported to the model, which decides what to do."""

import time
from typing import Any

from app.domain.errors import FetchFailed, FetchRefused, ModelsLoading, SearchUnavailable
from app.domain.evidence import SourceKind
from app.domain.ports import (
    HtmlExtractor,
    Indexer,
    PageFetcher,
    Retriever,
    SourceText,
    ToolSpec,
    WebSearch,
)
from app.domain.transcript import ToolCallRecord

from .base import (
    Gathered,
    ToolContext,
    ToolOutput,
    gather,
    is_weak,
    number,
    render_tool_result,
)
from .common import cut_at_paragraph, normalize_query, normalize_url, parse_evidence_id
from .wikipedia import SearchWikipediaTool

SEARCH_SPEC = ToolSpec(
    "search_web",
    "Search the web. Best for current events, products, comparisons and practical guides. "
    "Returns numbered evidence with snippets.",
    {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "3-8 specific search terms"}},
        "required": ["query"],
    },
)
READ_SPEC = ToolSpec(
    "read_result",
    'Open the full page behind a web evidence id (e.g. "E3") from an earlier search and '
    "return its most relevant passages. Only web evidence.",
    {
        "type": "object",
        "properties": {"evidence_id": {"type": "string", "description": 'e.g. "E3"'}},
        "required": ["evidence_id"],
    },
)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


class SearchWebTool:
    name = "search_web"
    family = SourceKind.WEB
    spec = SEARCH_SPEC

    def __init__(
        self,
        search: WebSearch,
        indexer: Indexer,
        retriever: Retriever,
        wikipedia: SearchWikipediaTool | None = None,
        *,
        max_results: int = 8,
        top_k: int = 6,
        snippet_chars: int = 900,
    ) -> None:
        self._search, self._indexer, self._retriever = search, indexer, retriever
        self._wikipedia = wikipedia
        self._max, self._k, self._snippet = max_results, top_k, snippet_chars

    def _out(
        self,
        ctx: ToolContext,
        query: str,
        status: str,
        started: float,
        gathered: Gathered | None = None,
        results: int = 0,
        error: str | None = None,
    ) -> ToolOutput:
        blocks = gathered.blocks if gathered else []
        record = ToolCallRecord(
            "researcher",
            self.name,
            {"query": query},
            status,  # type: ignore[arg-type]
            provider="searxng",
            results=results,
            evidence_ns=tuple(r.n for r, _ in blocks),
            error=error,
            duration_ms=_ms(started),
            weak=status == "ok" and is_weak(query, [c for _, c in blocks]),
        )
        content = render_tool_result(
            record,
            blocks,
            ctx.fence,
            new=gathered.new if gathered else 0,
            snippet_chars=self._snippet,
        )
        return ToolOutput(content, record)

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        started = time.perf_counter()
        query = normalize_query(args.get("query"))
        if query is None:
            return self._out(
                ctx, str(args.get("query"))[:300], "error", started,
                error="query must be 2-300 characters",
            )  # fmt: skip
        try:
            if not self._search.available():
                raise SearchUnavailable("breaker open")
            results = self._search.search(query, self._max)
        except SearchUnavailable:
            return self._fallback(ctx, query, started)
        if not results:  # engines answered: the model decides (no fallback on empty)
            return self._out(ctx, query, "empty", started)
        texts = [
            SourceText(
                ctx.run_scope,
                SourceKind.WEB,
                normalize_url(r.url),
                r.title,
                r.snippet or r.title,
                url=r.url,
            )  # fmt: skip
            for r in results
        ]
        try:
            gathered = gather(ctx, self._indexer, self._retriever, texts, query, self._k)
        except ModelsLoading:
            return self._out(ctx, query, "error", started, error="search index warming up")
        return self._out(ctx, query, "ok", started, gathered, len(results))

    def _fallback(self, ctx: ToolContext, query: str, started: float) -> ToolOutput:
        if self._wikipedia is None or SourceKind.WIKIPEDIA not in ctx.sources:
            return self._out(ctx, query, "error", started, error="web search unavailable")
        return self._wikipedia.search(
            query,
            ctx,
            started,
            tool=self.name,
            provider="searxng",
            fallback="wikipedia",
            note="Web search is unavailable right now. These results come from Wikipedia.",
        )


class ReadResultTool:
    """read_result(evidence_id) (SEARXNG_TOOL_PLAN §3.6): the model can only open a page a
    search already returned. Fetched through the SSRF guard, extracted, indexed under the
    SAME source key as its snippet, then the passages closest to what made the model pick it
    (title + snippet) are returned, the snippet itself excluded."""

    name = "read_result"
    family = SourceKind.WEB
    spec = READ_SPEC

    def __init__(
        self,
        fetcher: PageFetcher,
        extractor: HtmlExtractor,
        indexer: Indexer,
        retriever: Retriever,
        *,
        max_chars: int = 60_000,
        top_k: int = 6,
        snippet_chars: int = 900,
    ) -> None:
        self._fetcher, self._extractor = fetcher, extractor
        self._indexer, self._retriever = indexer, retriever
        self._max, self._k, self._snippet = max_chars, top_k, snippet_chars

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        started = time.perf_counter()
        n = parse_evidence_id(args.get("evidence_id"))
        label = f"E{n}" if n is not None else str(args.get("evidence_id"))[:20]

        def out(status, gathered=None, results=0, error=None, note=None):  # noqa: ANN001, ANN202
            blocks = gathered.blocks if gathered else []
            record = ToolCallRecord(
                "researcher", self.name, {"evidence_id": label}, status, provider="web-page",
                results=results, evidence_ns=tuple(r.n for r, _ in blocks), error=error,
                duration_ms=_ms(started),
            )  # fmt: skip
            content = render_tool_result(
                record, blocks, ctx.fence, new=gathered.new if gathered else 0, note=note,
                snippet_chars=self._snippet,
            )  # fmt: skip
            return ToolOutput(content, record)

        if n is None:
            return out("error", error="evidence_id must look like E3")
        ref = ctx.evidence_by_n(n)
        if ref is None:
            return out("refused", error=f"E{n} is not an evidence id from this run")
        if ref.source_kind is not SourceKind.WEB or not ref.url:
            return out("refused", error="read_result opens web results only")
        try:
            page = self._fetcher.fetch(ref.url)
        except FetchRefused:
            return out("refused", error="this page's address is not allowed")
        except FetchFailed as err:
            return out("error", error=err.reason)
        if page.media_type in ("text/html", "application/xhtml+xml"):
            text = self._extractor.html_to_markdown(page.text, page.url)
        elif page.media_type in ("text/plain", "text/markdown"):
            text = page.text
        else:
            return out("error", error=f"unsupported type {page.media_type}")
        text, truncated = cut_at_paragraph(text.strip(), self._max)
        if not text:
            return out("empty", note="The page has no readable text.")
        key = normalize_url(ref.url)
        source = SourceText(ctx.run_scope, SourceKind.WEB, key, ref.title, text, url=ref.url)
        try:
            stored = self._indexer.index([source])
            snippet = self._retriever.get([ref.chunk_id])
            query = f"{ref.title} {snippet[0].text if snippet else ''}".strip()
            hits = self._retriever.search(query, [ctx.run_scope], self._k + 1, source_keys=[key])
        except ModelsLoading:
            return out("error", error="search index warming up")
        chunks = [h.chunk for h in hits if h.chunk.id != ref.chunk_id][: self._k]
        note = "The page was truncated." if truncated else None
        return out("ok", number(ctx, chunks), results=len(stored), note=note)
