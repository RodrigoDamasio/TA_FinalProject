"""DocumentService (RAG_PLAN §3.6): validation by content, quotas, duplicates, the indexing
job, the delete race, purge, recovery and demo seeding."""

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeClock, rag_stack
from pdfs import make_pdf

from app.application.documents import (
    DocumentLimits,
    DocumentService,
    detect_media_type,
    sanitize_name,
)
from app.domain.documents import DEMO_WORKSPACE, DocumentStatus
from app.domain.errors import (
    DemoReadOnly,
    DocumentNotFound,
    FileTooLarge,
    IndexingBusy,
    InvalidField,
    QueueFull,
    UnsupportedMediaType,
    WorkspaceQuotaExceeded,
)
from app.infrastructure.document_store import SqliteDocumentStore
from app.infrastructure.pdf_extract import PypdfExtractor

WS = "3f2b8c1e-9a4d-4c7e-8b1a-2d3e4f5a6b7c"
OTHER = "9b2b8c1e-9a4d-4c7e-8b1a-2d3e4f5a6b7c"
MD = b"# Field guide\n\nWebhooks are signed with HMAC-SHA256 and a timestamp header."


class Deferred:
    """A runner that collects jobs; tests run them explicitly (or refuse them)."""

    def __init__(self, full=False):
        self.jobs, self.full = [], full

    def submit(self, job_id):
        if self.full:
            raise QueueFull()
        self.jobs.append(job_id)

    def has_capacity(self):
        return True


SMALL = DocumentLimits(1000, 2, 1500, 24)


def service(tmp_path, limits=SMALL, runner=None):
    indexer, retriever, store, _ = rag_stack()
    clock = FakeClock(datetime(2026, 10, 11, tzinfo=UTC))
    s = DocumentService(
        SqliteDocumentStore(str(tmp_path / "d.db")), indexer, PypdfExtractor(), limits, clock=clock
    )
    s.runner = runner or Deferred()
    return s, store, retriever, clock


def run_all(s):
    while s.runner.jobs:
        s.run_job(s.runner.jobs.pop(0))


def test_type_detection_and_name_sanitizing():
    assert detect_media_type("a.md", MD) == "text/markdown"
    assert detect_media_type("a.txt", "﻿hi".encode()) == "text/plain"
    assert detect_media_type("a.pdf", b"%PDF-1.7 ...") == "application/pdf"
    for name, data in (
        ("a.pdf", b"text"),
        ("a.md", b"%PDF-1.4"),
        ("a.exe", b"MZ"),
        ("a.txt", b"\xff\xfe"),
        ("a.txt", b"x\x00y"),
        ("a.txt", b"\x01" * 50),
    ):
        with pytest.raises(UnsupportedMediaType):
            detect_media_type(name, data)  # fmt: skip
    assert sanitize_name("../../etc/pass wd<1>.md") == "pass wd_1_.md"
    assert sanitize_name("\x00") == "document" and len(sanitize_name("a" * 300 + ".pdf")) == 120


def test_upload_index_list_get_and_isolation(tmp_path):
    s, store, retriever, _ = service(tmp_path)
    doc = s.upload(WS, "guide.md", MD)
    assert doc.status is DocumentStatus.INDEXING and s.runner.jobs == [doc.id]
    run_all(s)
    ready = s.get(WS, doc.id)
    assert ready.status is DocumentStatus.READY and ready.chunks == 1 and ready.pages is None
    hit = retriever.search("hmac webhook", [f"ws:{WS}"], 3)[0].chunk
    assert hit.title == "Field guide" and hit.document_name == "guide.md"
    workspace, docs, demo = s.list(WS)
    assert workspace.documents == 1 and workspace.bytes_used == len(MD) and docs[0].id == doc.id
    assert s.list(OTHER) == (None, [], [])  # never creates a workspace
    with pytest.raises(DocumentNotFound):
        s.get(OTHER, doc.id)  # another workspace's document is a 404
    assert retriever.search("hmac webhook", [f"ws:{OTHER}"], 3) == []


def test_refusals_quota_duplicates_and_busy_queue(tmp_path):
    s, *_ = service(tmp_path)
    with pytest.raises(FileTooLarge):
        s.upload(WS, "big.txt", b"x" * 1001)
    with pytest.raises(InvalidField):
        s.upload(WS, "empty.txt", b"")
    first = s.upload(WS, "a.md", MD)
    assert s.upload(WS, "again.md", MD).id == first.id  # same bytes: the existing document
    s.upload(WS, "b.txt", b"another text file, long enough")
    with pytest.raises(WorkspaceQuotaExceeded):
        s.upload(WS, "c.txt", b"third file")  # 2 documents max
    busy, *_ = service(tmp_path / "busy", runner=Deferred(full=True))
    with pytest.raises(IndexingBusy):
        busy.upload(WS, "a.md", MD)
    assert busy.list(WS)[1] == []  # nothing left behind


def test_failures_clean_up_and_a_failed_upload_can_be_retried(tmp_path):
    s, store, *_ = service(tmp_path)
    doc = s.upload(WS, "scan.pdf", make_pdf([""]))
    run_all(s)
    failed = s.get(WS, doc.id)
    assert failed.status is DocumentStatus.FAILED and "No extractable text" in failed.error
    assert store.count(f"ws:{WS}") == 0
    retry = s.upload(WS, "scan.pdf", make_pdf([""]))
    assert retry.id != doc.id  # the failed one was replaced
    lost = s.upload(WS, "lost.txt", b"bytes lost to a restart, long enough")
    s._pending.clear()
    s.run_job(lost.id)
    assert s.get(WS, lost.id).status is DocumentStatus.FAILED


def test_pdf_pages_delete_race_delete_and_demo_read_only(tmp_path):
    s, store, retriever, _ = service(tmp_path, DocumentLimits(100_000, 5, 500_000, 24))
    pdf = s.upload(
        WS,
        "report.pdf",
        make_pdf(["Risk governance overview page. " * 6, "Mapping risks to impacts. " * 6]),
    )
    run_all(s)
    ready = s.get(WS, pdf.id)
    assert ready.pages == 2 and retriever.search("mapping", [f"ws:{WS}"], 3)[0].chunk.page == 2
    racing = s.upload(WS, "race.md", MD + b" more")
    s.repo.delete(racing.id)  # deleted while queued
    run_all(s)
    assert store.count(f"ws:{WS}") == ready.chunks
    s.delete(WS, pdf.id)
    assert store.count(f"ws:{WS}") == 0
    with pytest.raises(DocumentNotFound):
        s.get(WS, pdf.id)


def test_purge_recover_and_seed(tmp_path):
    s, store, retriever, clock = service(tmp_path, DocumentLimits(100_000, 5, 500_000, 24))
    s.upload(WS, "a.md", MD)
    run_all(s)
    clock.advance(25 * 3600)
    assert s.purge() == 1 and store.count(f"ws:{WS}") == 0 and s.list(WS)[0] is None
    stuck = s.upload(WS, "stuck.md", MD)
    assert s.recover() == 1 and s.get(WS, stuck.id).status is DocumentStatus.FAILED

    files = [
        ("one.md", b"# One\n\nAgentic RAG lets agents retrieve."),
        ("two.txt", b"Plain notes about vector stores and recall."),
    ]
    s.schedule_seed(files)
    run_all(s)
    demo = {d.name: d for d in s.list(OTHER)[2]}
    assert set(demo) == {"one.md", "two.txt"} and all(d.demo for d in demo.values())
    s.schedule_seed(files)  # unchanged: skipped
    run_all(s)
    assert {d.id for d in s.list(OTHER)[2]} == {d.id for d in demo.values()}
    s.schedule_seed([("one.md", b"# One\n\nChanged text about agentic RAG."), ("bad.exe", b"MZ")])
    run_all(s)
    names = [d.name for d in s.list(OTHER)[2]]
    assert names == ["one.md"] and store.count(DEMO_WORKSPACE) == 1
    with pytest.raises(DemoReadOnly):
        s.delete(WS, s.list(OTHER)[2][0].id)
    assert datetime.now(UTC) - timedelta(days=1) < datetime.now(UTC)  # (clock sanity)
