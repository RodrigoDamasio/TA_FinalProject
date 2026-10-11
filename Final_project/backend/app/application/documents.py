"""DocumentService (RAG_PLAN §3.6): the document library. Uploads go to a private workspace
per browser (24 h, fixed) and are indexed by a background job; the demo library is seeded at
startup. The original bytes live only in memory until the job takes them. Owner: RAG.

Logs carry ids, sizes and counts only: never a file name, its text or a full workspace id."""

import builtins
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePath

from app.domain.documents import (
    DEMO_WORKSPACE,
    Document,
    DocumentStatus,
    Workspace,
    new_document_id,
    workspace_scope,
)
from app.domain.errors import (
    DemoReadOnly,
    DocumentNotFound,
    ExtractionError,
    FileTooLarge,
    IndexingBusy,
    InvalidField,
    ModelsLoading,
    QueueFull,
    UnsupportedMediaType,
    WorkspaceQuotaExceeded,
)
from app.domain.evidence import SourceKind
from app.domain.outbox import iso
from app.domain.ports import DocumentRepository, Indexer, JobRunner, PdfExtractor, SourceText

from .indexing.chunkers import CHUNKER_VERSION

logger = logging.getLogger("app.documents")
SEED_JOB = "seed:demo"
JOB_MODEL_WAIT_S = 600.0
_NAME_UNSAFE = re.compile(r"[^\w .()\-]")
_TEXT_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain"}


@dataclass(frozen=True)
class DocumentLimits:
    upload_max_bytes: int = 5 * 1024 * 1024
    workspace_max_docs: int = 10
    workspace_max_bytes: int = 20 * 1024 * 1024
    workspace_ttl_h: int = 24


def fingerprint(workspace_id: str) -> str:
    return hashlib.sha256(workspace_id.encode()).hexdigest()[:8]


def sanitize_name(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name
    base = "".join(ch for ch in base if ch.isprintable())
    base = _NAME_UNSAFE.sub("_", base).strip() or "document"
    if len(base) > 120:
        stem, dot, ext = base.rpartition(".")
        base = (stem[: 120 - len(ext) - 1] + "." + ext) if dot and len(ext) <= 10 else base[:120]
    return base


def detect_media_type(name: str, data: bytes) -> str:
    """Type by CONTENT, and the extension must agree (RAG_PLAN §3.6 step 5)."""
    suffix = PurePath(name).suffix.lower()
    if data.startswith(b"%PDF-"):
        if suffix != ".pdf":
            raise UnsupportedMediaType()
        return "application/pdf"
    if suffix not in _TEXT_TYPES:
        raise UnsupportedMediaType()
    try:
        text = data.removeprefix(b"\xef\xbb\xbf").decode("utf-8", errors="strict")
    except UnicodeDecodeError as err:
        raise UnsupportedMediaType() from err
    controls = sum(1 for ch in text if ord(ch) < 32 and ch not in "\n\r\t")
    if "\x00" in text or controls > len(text) / 100:
        raise UnsupportedMediaType()
    return _TEXT_TYPES[suffix]


def _markdown_title(text: str) -> str | None:
    for line in text.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()[:200]
    return None


class DocumentService:
    def __init__(
        self,
        repo: DocumentRepository,
        indexer: Indexer,
        extractor: PdfExtractor,
        limits: DocumentLimits,
        *,
        wait_ready: Callable[[float], bool] = lambda timeout: True,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        on_purge: Callable[[str], None] = lambda scope: None,
    ) -> None:
        self.repo, self._indexer, self._extractor, self.limits = repo, indexer, extractor, limits
        self._wait_ready, self._clock, self._on_purge = wait_ready, clock, on_purge
        self.runner: JobRunner | None = None  # set by the wiring (the indexing ThreadRunner)
        self._pending: dict[str, bytes] = {}
        self._demo_files: list[tuple[str, bytes]] = []
        self._lock = threading.Lock()

    # ---- upload -----------------------------------------------------------------------------

    def upload(self, workspace_id: str, name: str, data: bytes) -> Document:
        if len(data) > self.limits.upload_max_bytes:
            raise FileTooLarge(self.limits.upload_max_bytes)
        if not data:
            raise InvalidField("The file is empty.", "#/file")
        media_type = detect_media_type(name, data)
        clean_name = sanitize_name(name)
        now = self._clock()
        workspace = self._workspace(workspace_id, now)
        digest = hashlib.sha256(data).hexdigest()
        existing = self.repo.by_hash(workspace_id, digest)
        if existing is not None:
            if existing.status is not DocumentStatus.FAILED:
                return existing
            self.repo.delete(existing.id)
            workspace = self.repo.get_workspace(workspace_id) or workspace
        if (
            workspace.documents >= self.limits.workspace_max_docs
            or workspace.bytes_used + len(data) > self.limits.workspace_max_bytes
        ):
            raise WorkspaceQuotaExceeded(
                f"The workspace allows {self.limits.workspace_max_docs} documents and "
                f"{self.limits.workspace_max_bytes // (1024 * 1024)} MB."
            )
        if self.runner is None or not self.runner.has_capacity():
            raise IndexingBusy()
        doc = Document(
            new_document_id(), workspace_id, clean_name, media_type, len(data), digest,
            DocumentStatus.INDEXING, None, 0, None, iso(now), workspace.expires_at,
        )  # fmt: skip
        self.repo.save(doc)
        with self._lock:
            self._pending[doc.id] = data
        try:
            self.runner.submit(doc.id)
        except QueueFull as err:
            with self._lock:
                self._pending.pop(doc.id, None)
            self.repo.delete(doc.id)
            raise IndexingBusy() from err
        logger.info("upload doc=%s ws=%s bytes=%d", doc.id, fingerprint(workspace_id), len(data))
        return doc

    def _workspace(self, workspace_id: str, now: datetime) -> Workspace:
        workspace = self.repo.get_workspace(workspace_id)
        if workspace is not None and datetime.fromisoformat(workspace.expires_at) <= now:
            self._purge_workspace(workspace_id)
            workspace = None
        if workspace is None:
            expires = now + timedelta(hours=self.limits.workspace_ttl_h)
            workspace = Workspace(workspace_id, iso(now), iso(expires))
            self.repo.upsert_workspace(workspace)
        return workspace

    # ---- the indexing job -------------------------------------------------------------------

    def run_job(self, job_id: str) -> None:
        if job_id == SEED_JOB:
            self._seed()
            return
        with self._lock:
            data = self._pending.pop(job_id, None)
        doc = self.repo.get(job_id)
        if doc is None:
            return  # deleted before the job started
        if data is None:
            self._fail(doc, "The upload was interrupted; upload the file again.")
            return
        self._index(doc, data)

    def _index(self, doc: Document, data: bytes) -> None:
        started = time.perf_counter()
        scope = workspace_scope(doc.workspace_id)
        source_key = f"doc:{doc.id}"
        try:
            if not self._wait_ready(JOB_MODEL_WAIT_S):
                raise ModelsLoading()
            if doc.media_type == "application/pdf":
                text, pages = self._extractor.pdf_to_text(data)
                title = PurePath(doc.name).stem
            else:
                text, pages = data.removeprefix(b"\xef\xbb\xbf").decode("utf-8"), ()
                title = _markdown_title(text) or PurePath(doc.name).stem
            if len("".join(text.split())) < 20:
                raise ExtractionError("No extractable text (scanned or image-only PDF?).")
            chunks = self._indexer.index(
                [
                    SourceText(
                        scope,
                        SourceKind.DOCUMENTS,
                        source_key,
                        title,
                        text,
                        document_id=doc.id,
                        document_name=doc.name,
                        pages=pages,
                    )  # fmt: skip
                ]
            )
        except (ExtractionError, ModelsLoading) as err:
            self._indexer.remove(scope, source_key)
            self._fail(doc, str(err))
            return
        except Exception as err:  # never kill the runner; never leak details
            logger.exception("indexing doc=%s failed: %s", doc.id, type(err).__name__)
            self._indexer.remove(scope, source_key)
            self._fail(doc, "The document could not be indexed.")
            return
        if self.repo.get(doc.id) is None:  # deleted while indexing: clean up what we stored
            self._indexer.remove(scope, source_key)
            return
        doc.status, doc.chunks, doc.error = DocumentStatus.READY, len(chunks), None
        doc.pages = len(pages) if pages else None
        self.repo.save(doc)
        logger.info(
            "indexed doc=%s ws=%s pages=%s chunks=%d ms=%d", doc.id,
            fingerprint(doc.workspace_id), doc.pages, doc.chunks,
            int((time.perf_counter() - started) * 1000),
        )  # fmt: skip

    def _fail(self, doc: Document, reason: str) -> None:
        if self.repo.get(doc.id) is None:
            return
        doc.status, doc.error = DocumentStatus.FAILED, reason[:200]
        self.repo.save(doc)
        logger.info("indexing doc=%s failed", doc.id)

    # ---- reads and delete -------------------------------------------------------------------

    def list(self, workspace_id: str) -> tuple[Workspace | None, list[Document], list[Document]]:
        """(workspace or None, its documents newest first, the demo documents). Never creates."""
        workspace = self.repo.get_workspace(workspace_id)
        if workspace is not None and datetime.fromisoformat(workspace.expires_at) <= self._clock():
            self._purge_workspace(workspace_id)
            workspace = None
        documents = self.repo.list(workspace_id) if workspace is not None else []
        return workspace, documents, self.repo.list(DEMO_WORKSPACE)

    def get(self, workspace_id: str, document_id: str) -> Document:
        doc = self.repo.get(document_id)
        if doc is None or doc.workspace_id not in (workspace_id, DEMO_WORKSPACE):
            raise DocumentNotFound(document_id)
        return doc

    def delete(self, workspace_id: str, document_id: str) -> None:
        doc = self.get(workspace_id, document_id)
        if doc.demo:
            raise DemoReadOnly()
        self._indexer.remove(workspace_scope(workspace_id), f"doc:{doc.id}")
        self.repo.delete(doc.id)
        with self._lock:
            self._pending.pop(doc.id, None)

    # ---- housekeeping -----------------------------------------------------------------------

    def purge(self, now: datetime | None = None) -> int:
        expired = self.repo.expired(now or self._clock())
        for workspace in expired:
            self._purge_workspace(workspace.id)
        if expired:
            logger.info("purged workspaces=%d", len(expired))
        return len(expired)

    def _purge_workspace(self, workspace_id: str) -> None:
        scope = workspace_scope(workspace_id)
        self._indexer.remove(scope)
        self.repo.delete_workspace(workspace_id)
        self._on_purge(scope)

    def recover(self) -> int:
        """After a restart: documents still `indexing` lost their bytes."""
        stuck = self.repo.with_status(DocumentStatus.INDEXING)
        for doc in stuck:
            self._indexer.remove(workspace_scope(doc.workspace_id), f"doc:{doc.id}")
            self._fail(doc, "Interrupted by a server restart; upload the file again.")
        return len(stuck)

    # ---- the demo library -------------------------------------------------------------------

    def schedule_seed(self, files: builtins.list[tuple[str, bytes]]) -> None:
        """Queue seeding of the demo library (the first indexing job after startup)."""
        self._demo_files = list(files)
        if self.runner is not None:
            self.runner.submit(SEED_JOB)

    def _seed(self) -> None:
        """Unchanged files (same bytes, same CHUNKER_VERSION) are skipped; changed ones are
        re-indexed; removed ones are deleted."""
        now = iso(self._clock())
        current = {d.name: d for d in self.repo.list(DEMO_WORKSPACE)}
        wanted = set()
        for name, data in self._demo_files:
            wanted.add(name)
            digest = hashlib.sha256(data + CHUNKER_VERSION.encode()).hexdigest()
            old = current.get(name)
            if old is not None and old.sha256 == digest and old.status is DocumentStatus.READY:
                continue
            if old is not None:
                self._indexer.remove(DEMO_WORKSPACE, f"doc:{old.id}")
                self.repo.delete(old.id)
            try:
                media_type = detect_media_type(name, data)
            except UnsupportedMediaType:
                logger.warning("demo file skipped (type)")
                continue
            doc = Document(
                new_document_id(), DEMO_WORKSPACE, name, media_type, len(data), digest,
                DocumentStatus.INDEXING, None, 0, None, now, None,
            )  # fmt: skip
            self.repo.save(doc)
            self._index(doc, data)
        for name, old in current.items():
            if name not in wanted:
                self._indexer.remove(DEMO_WORKSPACE, f"doc:{old.id}")
                self.repo.delete(old.id)
        logger.info("demo library seeded files=%d", len(wanted))
