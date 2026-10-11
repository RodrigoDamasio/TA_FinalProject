"""The tool contract (BACKEND_PLAN §10.2, v1.1): ToolBox, the shared renderer, the four tools
and the RAG stubs underneath them (0 network)."""

from fakes import FakePageFetcher, FakeWebSearch, rag_stack

from app.application.security import DataFence
from app.application.tools.base import (
    ToolContext,
    ToolRegistry,
    evidence_range,
    is_weak,
    render_tool_result,
    where,
)
from app.application.tools.documents import SearchDocumentsTool
from app.application.tools.web import ReadResultTool, SearchWebTool
from app.application.tools.wikipedia import SearchWikipediaTool
from app.domain.errors import FetchRefused
from app.domain.evidence import REQUESTABLE_SOURCES, EvidenceChunk, SourceKind
from app.domain.ports import FetchedPage, SourceText
from app.domain.run import ResearchRun
from app.domain.transcript import ToolCallRecord
from app.infrastructure.fake_wikipedia import FakeWikipedia
from app.infrastructure.html_extract import TagStripExtractor

FENCE = DataFence(b"s", "q")


class Bench:
    def __init__(self, fetcher=None, wiki=None, search=None) -> None:
        self.run = ResearchRun.create("A question about RAG", 5, False, False)
        self.indexer, self.retriever, self.store, _ = rag_stack(300)
        self.ctx = ToolContext(
            self.run.id, f"run:{self.run.id}", ["ws:abc", "demo"], self.run.add_evidence,
            self.run.evidence_by_n, FENCE, REQUESTABLE_SOURCES,
            evidence_count=lambda: len(self.run.evidence),
        )  # fmt: skip
        self.wiki = SearchWikipediaTool(wiki or FakeWikipedia(), self.indexer, self.retriever)
        self.web = SearchWebTool(search or FakeWebSearch(), self.indexer, self.retriever, self.wiki)
        self.read = ReadResultTool(
            fetcher or FakePageFetcher(), TagStripExtractor(), self.indexer, self.retriever
        )
        self.docs = SearchDocumentsTool(self.indexer, self.retriever)


def chunk(n: int, kind=SourceKind.WEB, **kw) -> EvidenceChunk:
    base = dict(
        id=f"c{n}", scope="run:r", source_kind=kind, source_key=f"k{n}", title=f"T{n}",
        url=f"https://www.ex{n}.org/p", document_id=None, document_name=None, page=None,
        heading=None, position=0, text="retrieval augmented generation", content_hash="h",
        retrieved_at="t",
    )  # fmt: skip
    return EvidenceChunk(**(base | kw))


def test_renderer_layout_and_helpers():
    run = ResearchRun.create("q", 5, False, False)
    ref1, ref2 = run.add_evidence([chunk(1), chunk(2)])
    record = ToolCallRecord(
        "researcher", "search_web", {"query": 'rag "fine"'}, "ok",
        provider="searxng", results=8, evidence_ns=(1, 2), weak=True,
    )  # fmt: skip
    text = render_tool_result(record, [(ref1, chunk(1)), (ref2, chunk(2))], FENCE, new=2, note="N")
    lines = text.splitlines()
    assert (
        lines[0]
        == "RESULT search_web query=\"rag 'fine'\" provider=searxng results=8 evidence=E1-E2 new=2"
    )
    assert lines[1] == "NOTE N" and lines[2].startswith("HINT Few results")
    assert 'kind="evidence" ref="E1" source="web" title="T1" where="ex1.org"' in text
    empty = render_tool_result(ToolCallRecord("researcher", "search_web", {}, "empty"), [], FENCE)
    assert "HINT No results." in empty and "evidence=none" in empty
    err = render_tool_result(ToolCallRecord("r", "t", {}, "error", error="boom"), [], FENCE)
    assert "ERROR boom. Try another tool" in err
    assert evidence_range([2, 5, 9]) == "E2,E5,E9" and evidence_range([4]) == "E4"
    assert where(chunk(3, SourceKind.DOCUMENTS, document_name="h.pdf", page=4)) == "h.pdf p. 4"
    assert where(chunk(4, SourceKind.WIKIPEDIA)) == "wikipedia"
    assert is_weak("rag", [chunk(1)]) and not is_weak("retrieval generation", [chunk(1), chunk(2)])
    assert is_weak("kubernetes autoscaling operators", [chunk(1), chunk(2)])


def test_toolbox_offers_and_refuses_by_source():
    b = Bench()
    registry = ToolRegistry([b.web, b.wiki, b.docs, b.read])
    box = registry.box(b.ctx, frozenset({SourceKind.DOCUMENTS}), {})
    assert [s.name for s in box.offered()] == ["search_documents"]
    out = box.call("search_web", {"query": "rag"})
    assert out.record.status == "refused" and "Available: search_documents" in out.content
    assert {t["name"] for t in registry.specs()} == {
        "search_web", "search_wikipedia", "search_documents", "read_result",
    }  # fmt: skip


def test_search_web_indexes_numbers_and_is_idempotent():
    b = Bench()
    first = b.web.run({"query": "  rag   fine tuning "}, b.ctx)
    assert first.record.status == "ok" and first.record.args == {"query": "rag fine tuning"}
    assert first.record.evidence_ns and "new=" in first.content
    again = b.web.run({"query": "rag fine tuning"}, b.ctx)
    assert again.record.evidence_ns == first.record.evidence_ns and "new=0" in again.content
    assert b.web.run({"query": "x"}, b.ctx).record.status == "error"
    empty = Bench(search=FakeWebSearch(empty=True))
    assert empty.web.run({"query": "nothing here"}, empty.ctx).record.status == "empty"


def test_search_wikipedia_outcomes():
    b = Bench()
    ok = b.wiki.run({"query": "retrieval augmented generation"}, b.ctx)
    assert ok.record.status == "ok" and ok.record.provider == "wikipedia"
    assert b.wiki.run({"query": "zzzz qqqq"}, b.ctx).record.status == "empty"
    down = Bench(wiki=FakeWikipedia(down=True))
    assert down.wiki.run({"query": "rag"}, down.ctx).record.error == "wikipedia unavailable"
    assert b.wiki.run({"query": 3}, b.ctx).record.status == "error"


def test_read_result_only_opens_web_evidence_the_run_issued():
    page = FetchedPage("https://a.org/p", 200, "text/html", "<p>Full page about RAG.</p>")
    b = Bench(fetcher=FakePageFetcher({"https://a.org/p": page}))
    web = b.indexer.index([SourceText(b.ctx.run_scope, SourceKind.WEB, "https://a.org/p",
                                      "A", "snippet", url="https://a.org/p")])  # fmt: skip
    (ref,) = b.run.add_evidence(web)
    ok = b.read.run({"evidence_id": f"[{ref.label}]"}, b.ctx)
    assert ok.record.status == "ok" and ok.record.args == {"evidence_id": "E1"}
    assert "Full page about RAG." in ok.content and 1 not in ok.record.evidence_ns
    assert b.read.run({"evidence_id": "E99"}, b.ctx).record.status == "refused"
    assert b.read.run({"evidence_id": "nope"}, b.ctx).record.status == "error"
    doc = b.run.add_evidence([chunk(9, SourceKind.DOCUMENTS)])[0]
    assert b.read.run({"evidence_id": doc.label}, b.ctx).record.error.startswith("read_result")

    class Refusing(FakePageFetcher):
        def fetch(self, url):
            raise FetchRefused("private")

    b2 = Bench(fetcher=Refusing())
    (ref2,) = b2.run.add_evidence(web)
    assert b2.read.run({"evidence_id": ref2.label}, b2.ctx).record.status == "refused"
    b3 = Bench()  # FakePageFetcher: unknown URL → http 404
    (ref3,) = b3.run.add_evidence(web)
    assert b3.read.run({"evidence_id": ref3.label}, b3.ctx).record.error == "http 404"
    pdf = FetchedPage("https://a.org/p", 200, "application/pdf", "%PDF")
    b4 = Bench(fetcher=FakePageFetcher({"https://a.org/p": pdf}))
    (ref4,) = b4.run.add_evidence(web)
    assert "unsupported type" in b4.read.run({"evidence_id": ref4.label}, b4.ctx).record.error


def test_search_documents_copies_hits_into_the_run_scope():
    b = Bench()
    b.indexer.index([SourceText("demo", SourceKind.DOCUMENTS, "doc:d1", "Handbook",
                                "Agentic RAG lets the agent decide when to retrieve.",
                                document_id="d1", document_name="handbook.md")])  # fmt: skip
    out = b.docs.run({"query": "agentic rag retrieve"}, b.ctx)
    assert out.record.status == "ok" and out.record.provider == "documents"
    assert b.store.count(b.ctx.run_scope) == 1 and b.store.count("demo") == 1
    assert b.run.evidence[0].document_name == "handbook.md"
    assert b.docs.run({"query": "x"}, b.ctx).record.status == "error"


def test_indexer_and_store_maintenance():
    b = Bench()
    b.indexer.index([SourceText("demo", SourceKind.DOCUMENTS, "doc:1", "T", "one\n\ntwo")])
    assert b.indexer.remove("demo", "doc:1") == 1 and b.store.count("demo") == 0
    b.indexer.index([SourceText("demo", SourceKind.DOCUMENTS, "doc:2", "T", "three")])
    assert [c.text for c in b.store.iter_chunks("demo")] == ["three"]
    assert b.indexer.remove("demo") == 1 and b.indexer.adopt([], "run:x") == []
    assert b.retriever.search("q", [], 3) == [] and b.retriever.get(["missing"]) == []
