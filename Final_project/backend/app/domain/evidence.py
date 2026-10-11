"""Evidence: the unit the index stores, the tools retrieve and the report cites (BACKEND_PLAN
§5.3). A chunk is numbered within one run (EvidenceRef) the first time an agent sees it, and
cited as [E<n>]."""

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SourceKind(StrEnum):
    WEB = "web"
    WIKIPEDIA = "wikipedia"
    DOCUMENTS = "documents"
    MEMORY = "memory"  # v1.1: memory chunks only; never a requestable source


REQUESTABLE_SOURCES = frozenset({SourceKind.WEB, SourceKind.WIKIPEDIA, SourceKind.DOCUMENTS})


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def content_hash(text: str) -> str:
    """The deduplication key: case and whitespace do not matter (RAG_PLAN §3.1)."""
    return sha256_hex(" ".join(text.casefold().split()))


def chunk_id(scope: str, source_key: str, position: int, digest: str) -> str:
    return sha256_hex(f"{scope}\0{source_key}\0{position}\0{digest}")[:24]


def evidence_label(n: int) -> str:
    return f"E{n}"


@dataclass(frozen=True)
class EvidenceChunk:
    id: str
    scope: str  # "run:<id>" | "ws:<id>" | "demo" | "memory"
    source_kind: SourceKind
    source_key: str  # URL, "wikipedia:<page_id>", "doc:<document_id>", "run:<id>" (memory)
    title: str
    url: str | None
    document_id: str | None
    document_name: str | None
    page: int | None  # PDF page, 1-based
    heading: str | None  # "Guide > Setup"
    position: int  # order within the source
    text: str  # what the model sees
    content_hash: str
    retrieved_at: str  # ISO-8601 UTC


@dataclass(frozen=True)
class EvidenceRef:
    """A chunk numbered within ONE run. Holds no text: load it with Retriever.get."""

    n: int
    chunk_id: str
    source_kind: SourceKind
    title: str
    url: str | None
    document_name: str | None
    page: int | None

    @property
    def label(self) -> str:
        return evidence_label(self.n)


def ref_to_dict(ref: EvidenceRef) -> dict[str, Any]:
    return {
        "n": ref.n,
        "chunk_id": ref.chunk_id,
        "source_kind": ref.source_kind.value,
        "title": ref.title,
        "url": ref.url,
        "document_name": ref.document_name,
        "page": ref.page,
    }


def ref_from_dict(d: dict[str, Any]) -> EvidenceRef:
    return EvidenceRef(
        d["n"],
        d["chunk_id"],
        SourceKind(d["source_kind"]),
        d["title"],
        d["url"],
        d["document_name"],
        d["page"],
    )
