"""The Indexer (RAG_PLAN §3.3): dedup rules per scope kind, the embedding cache, adopt,
remove, and waiting for the models."""

import pytest
from fakes import DictEmbeddingCache, HashingEmbedder, InMemoryVectorStore, rag_stack

from app.application.embedding import CachedEmbeddings, StaticModels, ready_embedder
from app.application.indexing.chunkers import ChunkOptions
from app.application.indexing.indexer import HybridIndexer
from app.application.retrieval.bm25 import ScopedBM25
from app.domain.errors import ModelsLoading
from app.domain.evidence import SourceKind
from app.domain.ports import SourceText


def web(scope, url, text):
    return SourceText(scope, SourceKind.WEB, url, url, text, url=url)


def test_run_scopes_dedup_across_sources_documents_only_within_one():
    indexer, _, store, _ = rag_stack()
    a = indexer.index([web("run:1", "https://a", "Same snippet text.")])
    b = indexer.index([web("run:1", "https://b", "same   SNIPPET text.")])
    assert a[0].id == b[0].id and store.count("run:1") == 1  # stored once, returned twice
    d1 = SourceText("demo", SourceKind.DOCUMENTS, "doc:1", "T", "Shared paragraph.")
    d2 = SourceText("demo", SourceKind.DOCUMENTS, "doc:2", "T", "Shared paragraph.")
    indexer.index([d1, d2])
    assert store.count("demo") == 2  # deleting doc:1 must not remove doc:2's text
    assert indexer.index([d1])[0].source_key == "doc:1" and store.count("demo") == 2


def test_embeddings_come_from_the_cache_on_adopt_and_reindex():
    cache = DictEmbeddingCache()
    store, embedder = InMemoryVectorStore(), HashingEmbedder()
    bm25 = ScopedBM25(
        lambda s: ((c.id, c.text, c.source_key, c.position) for c in store.iter_chunks(s))
    )
    calls = {"n": 0}
    original = embedder.embed_documents

    def counting(texts):
        calls["n"] += len(texts)
        return original(texts)

    embedder.embed_documents = counting
    indexer = HybridIndexer(
        store, StaticModels(embedder), CachedEmbeddings(cache), bm25, ChunkOptions()
    )
    chunks = indexer.index([SourceText("demo", SourceKind.DOCUMENTS, "doc:1", "T", "Text one.")])
    copies = indexer.adopt(chunks, "run:7")
    assert calls["n"] == 1 and copies[0].scope == "run:7" and copies[0].id != chunks[0].id
    assert indexer.adopt(chunks, "run:7")[0].id == copies[0].id and store.count("run:7") == 1
    assert indexer.remove("run:7") == 1 and store.count("run:7") == 0
    assert indexer.index([]) == []


def test_waiting_for_models_raises_models_loading():
    class Loading(StaticModels):
        def wait_ready(self, timeout_s):
            return False

    store = InMemoryVectorStore()
    bm25 = ScopedBM25(lambda s: [])
    indexer = HybridIndexer(
        store, Loading(HashingEmbedder()), CachedEmbeddings(DictEmbeddingCache()), bm25,
        ChunkOptions(), wait_s=0,
    )  # fmt: skip
    with pytest.raises(ModelsLoading):
        indexer.index([web("run:1", "https://a", "text")])
    assert ready_embedder(StaticModels(HashingEmbedder()), 0).name == "fake-hashing-256"


def test_remove_keeps_bm25_in_sync():
    indexer, retriever, _, bm25 = rag_stack()
    indexer.index([SourceText("demo", SourceKind.DOCUMENTS, "doc:1", "T", "unique zebra term")])
    assert retriever.search("zebra", ["demo"], 3)
    indexer.remove("demo", "doc:1")
    assert bm25.search("demo", "zebra", 3) == [] and retriever.search("zebra", ["demo"], 3) == []
