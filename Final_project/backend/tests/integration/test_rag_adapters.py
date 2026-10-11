"""RAG adapters: PDF extraction (generated PDFs), Chroma in a temp dir, the SQLite embedding
cache, and the ONNX model loader with a fake loader (the real model is marked `models`)."""

import pytest
from pdfs import make_pdf

from app.domain.errors import ExtractionError, ModelsLoading
from app.domain.evidence import EvidenceChunk, SourceKind
from app.infrastructure.chroma_store import ChromaVectorStore, collection_name
from app.infrastructure.embedding_cache import SqliteEmbeddingCache
from app.infrastructure.fastembed_models import OnnxModels
from app.infrastructure.pdf_extract import PypdfExtractor


def test_pdf_pages_offsets_running_lines_and_hyphens():
    data = make_pdf(
        [
            "ACME\nFirst page about risk.\n1",
            "ACME\nSecond page.\n2",
            "ACME\nThird gov-\nernance.\n3",
        ]
    )
    text, pages = PypdfExtractor().pdf_to_text(data)
    assert text == "First page about risk.\n\nSecond page.\n\nThird governance."
    assert pages == ((1, 0), (2, 24), (3, 38))
    assert text[pages[1][1] :].startswith("Second page.")


def test_pdf_limits_and_refusals():
    with pytest.raises(ExtractionError, match="pages"):
        PypdfExtractor(max_pages=1).pdf_to_text(make_pdf(["a", "b"]))
    with pytest.raises(ExtractionError, match="too much text"):
        PypdfExtractor(max_chars=5).pdf_to_text(make_pdf(["more than five characters"]))
    with pytest.raises(ExtractionError, match="password"):
        PypdfExtractor().pdf_to_text(make_pdf(["secret"], password="pw"))
    with pytest.raises(ExtractionError, match="could not be read"):
        PypdfExtractor().pdf_to_text(b"%PDF-1.4 garbage")


def chunk(i: int, scope: str, key: str, **kw) -> EvidenceChunk:
    base = dict(
        id=f"c{i}", scope=scope, source_kind=SourceKind.DOCUMENTS, source_key=key, title="T",
        url=None, document_id="d", document_name="a.pdf", page=None, heading=None,
        position=i, text=f"text {i}", content_hash="h", retrieved_at="t",
    )  # fmt: skip
    return EvidenceChunk(**(base | kw))


def test_chroma_store_scopes_filters_and_deletes(tmp_path):
    store = ChromaVectorStore(str(tmp_path / "chroma"), "BAAI/bge-small-en-v1.5")
    assert collection_name("BAAI/bge-small-en-v1.5") == "evidence-baai-bge-small-en-v1-5"
    assert store.query([1.0, 0.0], ["demo"], 3) == []  # empty collection
    store.upsert(
        [chunk(1, "demo", "doc:1", page=4, heading="A > B"), chunk(2, "demo", "doc:2"),
         chunk(3, "ws:x", "doc:3")],
        [[1.0, 0.0], [0.9, 0.1], [0.0, 1.0]],
    )  # fmt: skip
    assert [i for i, _ in store.query([1.0, 0.0], ["demo"], 5)] == ["c1", "c2"]
    assert [i for i, _ in store.query([1.0, 0.0], ["demo", "ws:x"], 5, ["doc:3"])] == ["c3"]
    assert store.query([1.0, 0.0], ["demo"], 5, []) == []
    got = store.get(["c2", "c1", "missing"])
    assert [c.id for c in got] == ["c2", "c1"] and got[1].page == 4 and got[1].heading == "A > B"
    assert [c.id for c in store.iter_chunks("ws:x")] == ["c3"] and store.count("demo") == 2
    assert store.delete_source("demo", "doc:1") == 1 and store.delete_scope("ws:x") == 1
    assert store.count() == 1
    reopened = ChromaVectorStore(str(tmp_path / "chroma"), "BAAI/bge-small-en-v1.5")
    assert [c.id for c in reopened.iter_chunks("demo")] == ["c2"]  # persisted


def test_sqlite_embedding_cache(tmp_path):
    cache = SqliteEmbeddingCache(str(tmp_path / "e.db"))
    cache.put_many("m", {"k": [0.5, 0.25]})
    assert cache.get_many("m", ["k", "x"]) == {"k": [0.5, 0.25]}
    assert cache.stats() == {"stored": 1, "hits": 1, "misses": 1}


def test_onnx_models_load_in_the_background_with_a_fake_loader():
    class Fake:
        name, dim, max_tokens = "fake", 2, 512

    loaded = OnnxModels("e", "", "models", 1, 1, loader=lambda kind: Fake())
    with pytest.raises(ModelsLoading):
        loaded.embedder()
    assert loaded.status() == "loading" and loaded.reranker() == (None, "disabled")
    loaded.start()
    assert loaded.wait_ready(5) and loaded.wait_all(5) and loaded.status() == "ready"
    assert loaded.embedder().name == "fake"

    def boom(kind):
        raise OSError("no network")

    failed = OnnxModels("e", "r", "models", 1, 1, loader=boom)
    failed.start()
    assert failed.wait_all(5) and failed.status() == "failed"
    assert failed.reranker() == (None, "unavailable")
