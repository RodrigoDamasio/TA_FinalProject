"""The real ONNX embedder (marker `models`): run with `pytest -m models`. Downloads
BAAI/bge-small-en-v1.5 once into MODELS_PATH. 0 API calls."""

import os
import time

import pytest
from fakes import DictEmbeddingCache

from app.api.dependencies import demo_library_files
from app.application.documents import DocumentLimits, DocumentService
from app.application.embedding import CachedEmbeddings
from app.application.indexing.chunkers import ChunkOptions, embed_text
from app.application.indexing.indexer import HybridIndexer
from app.application.retrieval.bm25 import ScopedBM25
from app.application.retrieval.retriever import HybridRetriever
from app.domain.documents import DEMO_WORKSPACE, DocumentStatus
from app.infrastructure.document_store import SqliteDocumentStore
from app.infrastructure.fastembed_models import OnnxModels
from app.infrastructure.memory_vector_store import InMemoryVectorStore
from app.infrastructure.pdf_extract import PypdfExtractor

pytestmark = pytest.mark.models


def test_real_library_fits_the_embedder_window_and_retrieves(tmp_path):
    models = OnnxModels("BAAI/bge-small-en-v1.5", "", os.getenv("MODELS_PATH", "models"), 2, 4)
    models.start()
    assert models.wait_ready(600), models.status()
    store = InMemoryVectorStore()
    bm25 = ScopedBM25(
        lambda s: ((c.id, embed_text(c), c.source_key, c.position) for c in store.iter_chunks(s))
    )
    embeddings = CachedEmbeddings(DictEmbeddingCache())
    indexer = HybridIndexer(store, models, embeddings, bm25, ChunkOptions())
    retriever = HybridRetriever(store, models, embeddings, bm25)

    class Inline:
        def submit(self, job_id):
            service.run_job(job_id)

        def has_capacity(self):
            return True

    service = DocumentService(
        SqliteDocumentStore(str(tmp_path / "d.db")), indexer, PypdfExtractor(), DocumentLimits()
    )
    service.runner = Inline()
    started = time.perf_counter()
    service.schedule_seed(demo_library_files("samples/library"))
    seconds = time.perf_counter() - started
    docs = service.repo.list(DEMO_WORKSPACE)
    assert len(docs) == 8 and all(d.status is DocumentStatus.READY for d in docs)
    embedder = models.embedder()
    longest = max(embedder.count_tokens(embed_text(c)) for c in store.iter_chunks(DEMO_WORKSPACE))
    print(f"\nchunks={store.count(DEMO_WORKSPACE)} seed_s={seconds:.1f} longest={longest} tokens")
    assert longest <= embedder.max_tokens  # nothing is silently truncated (M4 lesson)
    hits = retriever.search(
        "How should a receiver verify a webhook signature?", [DEMO_WORKSPACE], 6
    )
    assert "standard-webhooks-spec.md" in {h.chunk.document_name for h in hits}
    para = retriever.search(
        "ways to stop attackers from manipulating a language model", [DEMO_WORKSPACE], 6
    )
    assert "securing-ai-applications.md" in {h.chunk.document_name for h in para}
