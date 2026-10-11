"""The document library (BACKEND_PLAN §5.5, v1.1 §24): uploads live in a per-browser
workspace for WORKSPACE_TTL_H; the demo library uses workspace_id "demo". The original bytes
are never kept."""

import re
import secrets
from dataclasses import dataclass
from enum import StrEnum

DEMO_WORKSPACE = "demo"
_UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def valid_workspace_id(value: str | None) -> bool:
    """A lowercase UUIDv4 (crypto.randomUUID()). 'demo' never matches, so nobody can write it."""
    return bool(value) and _UUID4.match(value or "") is not None


def workspace_scope(workspace_id: str) -> str:
    return DEMO_WORKSPACE if workspace_id == DEMO_WORKSPACE else f"ws:{workspace_id}"


def new_document_id() -> str:
    return "doc_" + secrets.token_hex(6)


class DocumentStatus(StrEnum):
    INDEXING = "indexing"
    READY = "ready"
    FAILED = "failed"


@dataclass
class Document:
    id: str
    workspace_id: str
    name: str  # sanitized, ≤ 120 chars
    media_type: str  # text/markdown | text/plain | application/pdf
    size_bytes: int
    sha256: str
    status: DocumentStatus
    pages: int | None
    chunks: int
    error: str | None
    created_at: str
    expires_at: str | None  # None for demo

    @property
    def demo(self) -> bool:
        return self.workspace_id == DEMO_WORKSPACE


@dataclass
class Workspace:
    id: str
    created_at: str
    expires_at: str
    bytes_used: int = 0  # computed from its documents
    documents: int = 0  # computed from its documents
