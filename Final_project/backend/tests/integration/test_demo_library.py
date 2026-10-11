"""The real demo library seeds and is searchable (fake embedder, 0 network)."""

import time
from datetime import UTC, datetime

from fakes import FakeClock, rag_stack

from app.api.dependencies import demo_library_files
from app.application.documents import DocumentLimits, DocumentService
from app.domain.documents import DEMO_WORKSPACE, DocumentStatus
from app.infrastructure.document_store import SqliteDocumentStore
from app.infrastructure.pdf_extract import PypdfExtractor


class Inline:
    def __init__(self, service):
        self.service = service

    def submit(self, job_id):
        self.service.run_job(job_id)

    def has_capacity(self):
        return True


def test_the_demo_library_seeds_and_is_searchable(tmp_path):
    files = demo_library_files("samples/library")
    assert len(files) == 8 and "LICENSES.md" not in {n for n, _ in files}
    indexer, retriever, store, _ = rag_stack(1400, 400)
    s = DocumentService(
        SqliteDocumentStore(str(tmp_path / "d.db")), indexer, PypdfExtractor(), DocumentLimits(),
        clock=FakeClock(datetime(2026, 10, 11, tzinfo=UTC)),
    )  # fmt: skip
    s.runner = Inline(s)
    started = time.perf_counter()
    s.schedule_seed(files)
    seconds = time.perf_counter() - started
    docs = {d.name: d for d in s.repo.list(DEMO_WORKSPACE)}
    assert all(d.status is DocumentStatus.READY for d in docs.values()), {
        n: d.error for n, d in docs.items()
    }
    assert docs["nist-ai-rmf-1.0.pdf"].pages == 48 and store.count(DEMO_WORKSPACE) > 100
    assert seconds < 30
    hits = retriever.search("webhook-signature header HMAC", [DEMO_WORKSPACE], 6)
    assert any(h.chunk.document_name == "standard-webhooks-spec.md" for h in hits)
    govern = retriever.search("GOVERN function categories", [DEMO_WORKSPACE], 6)
    assert any(h.chunk.page for h in govern)  # PDF evidence carries page numbers
    planted = retriever.search("ZEBRA-HARBOR-7Q", [DEMO_WORKSPACE], 3)  # an exact identifier:
    assert "vector-store-operations-notes.txt" in {h.chunk.document_name for h in planted}
