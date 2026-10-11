"""Per-scope BM25 (the M4 leak fix), two-stage RRF, caps and rerank (RAG_PLAN §3.4)."""

from fakes import rag_stack

from app.application.retrieval.bm25 import BM25Index, ScopedBM25, tokenize
from app.application.retrieval.retriever import HybridRetriever
from app.domain.evidence import SourceKind
from app.domain.ports import SourceText


def doc(scope: str, key: str, text: str, title: str = "T") -> SourceText:
    return SourceText(scope, SourceKind.DOCUMENTS, key, title, text, document_id=key)


def test_prose_tokenizer_keeps_identifiers_and_code_words():
    assert tokenize("The max_tokens field and the processOrder function return") == [
        "max", "tokens", "max_tokens", "field", "process", "order", "processorder",
        "function", "return",
    ]  # fmt: skip
    assert "function" not in tokenize("function", mode="code")


def test_bm25_scores_in_one_scope_ignore_other_scopes():
    """Regression for M4's cross-codebase leak: adding to scope B must not change scope A."""
    loader_data = {
        "a": [("a1", "webhook signature header", "k1", 0), ("a2", "other text", "k2", 0)]
    }
    scoped = ScopedBM25(lambda scope: loader_data.get(scope, []), pinned=("a",))
    before = scoped.search("a", "webhook signature", 5)
    for i in range(30):
        scoped.index("b").add(f"b{i}", "webhook webhook webhook", f"kb{i}")
    after = scoped.search("a", "webhook signature", 5)
    assert before == after and after[0].chunk_id == "a1"


def test_bm25_filters_removal_and_lazy_build():
    index = BM25Index()
    index.add("1", "hnsw ef parameter", "doc:1")
    index.add("2", "hnsw graph layers", "doc:2")
    assert [h.chunk_id for h in index.search("hnsw", 5, ["doc:2"])] == ["2"]
    assert index.remove_source("doc:1") == 1 and len(index) == 1
    index.remove("2")
    assert index.search("hnsw", 5) == [] and index.search("", 5) == []
    built = ScopedBM25(lambda s: [("x", "lazy build text", "k", 0)], capacity=1)
    assert built.search("run:1", "lazy", 3)[0].chunk_id == "x"
    built.index("run:2")  # evicts run:1 from the LRU of 1
    assert not built.loaded("run:1") and built.loaded("run:2")
    built.evict("run:2")
    assert not built.loaded("run:2")


def test_hybrid_search_caps_sources_and_respects_filters():
    indexer, retriever, store, _ = rag_stack(200, 50)
    long = "\n\n".join(f"Section {i} about vector databases and HNSW indexes." for i in range(12))
    indexer.index([doc("demo", "doc:1", long), doc("demo", "doc:2", "HNSW in another doc.")])
    hits = retriever.search("HNSW vector databases", ["demo"], 6)
    per_doc = [h.chunk.source_key for h in hits]
    assert per_doc.count("doc:1") <= 3 and "doc:2" in per_doc
    only = retriever.search("HNSW", ["demo"], 6, source_keys=["doc:2"])
    assert {h.chunk.source_key for h in only} == {"doc:2"}
    assert retriever.search("  ", ["demo"], 3) == [] and retriever.search("x", [], 3) == []
    ids = [h.chunk.id for h in hits[:2]]
    assert [c.id for c in retriever.get(list(reversed(ids)))] == list(reversed(ids))


def test_modes_and_rerank():
    indexer, retriever, store, bm25 = rag_stack()
    indexer.index([doc("demo", "doc:1", "alpha beta"), doc("demo", "doc:2", "gamma delta")])

    class Reverse:
        name = "reverse"

        def score(self, query, texts):
            return [float(i) for i in range(len(texts))]  # the last one wins

    class Models:
        def __init__(self, inner):
            self.inner = inner

        def embedder(self):
            return self.inner.embedder()

        def reranker(self):
            return Reverse(), None

    base = retriever
    for mode in ("vector", "bm25"):
        r = HybridRetriever(store, base._models, base._embeddings, bm25, mode=mode)
        assert r.search("alpha", ["demo"], 1)[0].chunk.source_key == "doc:1"
    reranked = HybridRetriever(
        store, Models(base._models), base._embeddings, bm25, mode="hybrid_rerank"
    )
    hits = reranked.search("alpha beta gamma", ["demo"], 2)
    assert len(hits) == 2 and hits[0].score == 1.0
    explained = base.explain("alpha", ["demo"], 2)
    assert explained[0].bm25_matched and explained[0].cosine is not None
