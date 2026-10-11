"""SqliteDocumentStore (DocumentRepository, v1.1 G5): documents and workspaces. Workspace
usage is computed from its documents (failed ones count toward neither). Bound parameters
only. Owner: RAG."""

import builtins
import threading
from datetime import datetime

from app.domain.documents import Document, DocumentStatus, Workspace
from app.domain.outbox import iso

from . import database

_UPSERT_DOC = (
    "INSERT OR REPLACE INTO documents (id, workspace_id, name, media_type, size_bytes, sha256, "
    "status, pages, chunks, error, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
    "?, ?)"
)
_GET_DOC = "SELECT * FROM documents WHERE id = ?"
_LIST_DOCS = "SELECT * FROM documents WHERE workspace_id = ? ORDER BY created_at DESC, id"
_BY_HASH = "SELECT * FROM documents WHERE workspace_id = ? AND sha256 = ?"
_WITH_STATUS = "SELECT * FROM documents WHERE status = ?"
_DELETE_DOC = "DELETE FROM documents WHERE id = ?"
_GET_WS = "SELECT id, created_at, expires_at FROM workspaces WHERE id = ?"
_UPSERT_WS = "INSERT OR REPLACE INTO workspaces (id, created_at, expires_at) VALUES (?, ?, ?)"
_DELETE_WS = "DELETE FROM workspaces WHERE id = ?"
_DELETE_WS_DOCS = "DELETE FROM documents WHERE workspace_id = ?"
_EXPIRED_WS = "SELECT id, created_at, expires_at FROM workspaces WHERE expires_at <= ?"
_USAGE = (
    "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes FROM documents "
    "WHERE workspace_id = ? AND status != ?"
)


def _document(r) -> Document:  # noqa: ANN001 — sqlite3.Row
    return Document(
        r["id"], r["workspace_id"], r["name"], r["media_type"], r["size_bytes"], r["sha256"],
        DocumentStatus(r["status"]), r["pages"], r["chunks"], r["error"], r["created_at"],
        r["expires_at"],
    )  # fmt: skip


class SqliteDocumentStore:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        database.init(path)

    def _conn(self):  # noqa: ANN202
        return database.connect(self._path)

    def save(self, d: Document) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                _UPSERT_DOC,
                (
                    d.id, d.workspace_id, d.name, d.media_type, d.size_bytes, d.sha256,
                    d.status.value, d.pages, d.chunks, d.error, d.created_at, d.expires_at,
                ),
            )  # fmt: skip

    def get(self, document_id: str) -> Document | None:
        with self._conn() as conn:
            row = conn.execute(_GET_DOC, (document_id,)).fetchone()
        return _document(row) if row else None

    def list(self, workspace_id: str) -> list[Document]:
        with self._conn() as conn:
            return [_document(r) for r in conn.execute(_LIST_DOCS, (workspace_id,)).fetchall()]

    def by_hash(self, workspace_id: str, sha256: str) -> Document | None:
        with self._conn() as conn:
            row = conn.execute(_BY_HASH, (workspace_id, sha256)).fetchone()
        return _document(row) if row else None

    def delete(self, document_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_DELETE_DOC, (document_id,))

    def with_status(self, status: DocumentStatus) -> builtins.list[Document]:
        with self._conn() as conn:
            return [_document(r) for r in conn.execute(_WITH_STATUS, (status.value,)).fetchall()]

    def get_workspace(self, workspace_id: str) -> Workspace | None:
        with self._conn() as conn:
            row = conn.execute(_GET_WS, (workspace_id,)).fetchone()
            if row is None:
                return None
            usage = conn.execute(_USAGE, (workspace_id, DocumentStatus.FAILED.value)).fetchone()
        return Workspace(
            row["id"], row["created_at"], row["expires_at"], usage["bytes"], usage["n"]
        )

    def upsert_workspace(self, w: Workspace) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_UPSERT_WS, (w.id, w.created_at, w.expires_at))

    def delete_workspace(self, workspace_id: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(_DELETE_WS_DOCS, (workspace_id,))
            conn.execute(_DELETE_WS, (workspace_id,))

    def expired(self, now: datetime) -> builtins.list[Workspace]:
        with self._conn() as conn:
            rows = conn.execute(_EXPIRED_WS, (iso(now),)).fetchall()
        return [Workspace(r["id"], r["created_at"], r["expires_at"]) for r in rows]
