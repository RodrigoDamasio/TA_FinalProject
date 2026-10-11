"""ChromaVectorStore (M4, adapted): one persistent collection per embedding model, cosine
HNSW, our own vectors (Chroma's embedding function is never used). `codebase` became `scope`
and a `source_key` filter was added (v1.1 G1). The stored document is the chunk body; every
other field is flat metadata, so `embed_text` is rebuilt exactly. Owner: RAG."""

import re
import threading
from collections.abc import Iterator
from typing import Any

import chromadb
from chromadb.config import Settings

from app.application.indexing.chunkers import CHUNKER_VERSION
from app.domain.evidence import EvidenceChunk, SourceKind

PAGE = 500
_OPTIONAL = ("url", "document_id", "document_name", "page", "heading")


def collection_name(model: str) -> str:
    """Chroma names: 3-63 chars of [a-zA-Z0-9._-], starting and ending alphanumeric."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", model).strip("-").lower()
    return f"evidence-{slug}"[:63].rstrip("-.")


def to_metadata(c: EvidenceChunk) -> dict[str, Any]:
    """Flat scalars only (Chroma rejects lists and None)."""
    meta: dict[str, Any] = {
        "scope": c.scope,
        "source_kind": c.source_kind.value,
        "source_key": c.source_key,
        "title": c.title,
        "position": c.position,
        "content_hash": c.content_hash,
        "retrieved_at": c.retrieved_at,
        "chunker_version": CHUNKER_VERSION,
    }
    for name in _OPTIONAL:
        value = getattr(c, name)
        if value is not None:
            meta[name] = value
    return meta


def from_metadata(chunk_id: str, text: str, m: dict[str, Any]) -> EvidenceChunk:
    page = m.get("page")
    return EvidenceChunk(
        chunk_id,
        str(m["scope"]),
        SourceKind(m["source_kind"]),
        str(m["source_key"]),
        str(m["title"]),
        m.get("url"),
        m.get("document_id"),
        m.get("document_name"),
        int(page) if page is not None else None,
        m.get("heading"),
        int(m["position"]),
        text,
        str(m["content_hash"]),
        str(m["retrieved_at"]),
    )


def _where(
    scopes: list[str] | None = None, source_keys: list[str] | None = None
) -> dict[str, Any] | None:
    clauses: list[dict[str, Any]] = []
    for field, values in (("scope", scopes), ("source_key", source_keys)):
        if values is not None:
            clauses.append({field: values[0]} if len(values) == 1 else {field: {"$in": values}})
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


class ChromaVectorStore:
    def __init__(self, path: str, model: str) -> None:
        self._client = chromadb.PersistentClient(
            path=path, settings=Settings(anonymized_telemetry=False)
        )
        self._collection = self._client.get_or_create_collection(
            collection_name(model), metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        self._lock = threading.Lock()  # one writer at a time

    def upsert(self, chunks: list[EvidenceChunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        with self._lock:
            for start in range(0, len(chunks), PAGE):
                batch = chunks[start : start + PAGE]
                self._collection.upsert(
                    ids=[c.id for c in batch],
                    embeddings=vectors[start : start + PAGE],
                    documents=[c.text for c in batch],
                    metadatas=[to_metadata(c) for c in batch],
                )

    def query(
        self,
        vector: list[float],
        scopes: list[str],
        n: int,
        source_keys: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        if not scopes or n <= 0 or (source_keys is not None and not source_keys):
            return []
        if self._collection.count() == 0:
            return []
        result = self._collection.query(
            query_embeddings=[vector],
            n_results=n,
            where=_where(scopes, source_keys),
            include=["distances"],
        )
        ids = result["ids"][0] if result["ids"] else []
        distances = (result.get("distances") or [[]])[0]
        return list(zip(ids, (float(d) for d in distances), strict=True))

    def get(self, ids: list[str]) -> list[EvidenceChunk]:
        if not ids:
            return []
        result = self._collection.get(ids=ids, include=["documents", "metadatas"])
        by_id = {c.id: c for c in _chunks(result)}
        return [by_id[i] for i in ids if i in by_id]

    def iter_chunks(self, scope: str) -> Iterator[EvidenceChunk]:
        offset = 0
        while True:
            result = self._collection.get(
                where=_where([scope]), include=["documents", "metadatas"], limit=PAGE,
                offset=offset,
            )  # fmt: skip
            chunks = _chunks(result)
            yield from chunks
            if len(chunks) < PAGE:
                return
            offset += PAGE

    def _delete(self, where: dict[str, Any] | None) -> int:
        with self._lock:
            ids = list(self._collection.get(where=where, include=[])["ids"])
            if ids:
                self._collection.delete(ids=ids)
            return len(ids)

    def delete_scope(self, scope: str) -> int:
        return self._delete(_where([scope]))

    def delete_source(self, scope: str, source_key: str) -> int:
        return self._delete(_where([scope], [source_key]))

    def count(self, scope: str | None = None) -> int:
        if scope is None:
            return self._collection.count()
        return len(self._collection.get(where=_where([scope]), include=[])["ids"])


def _chunks(result: Any) -> list[EvidenceChunk]:
    documents = result.get("documents") or []
    metadatas = result.get("metadatas") or []
    return [
        from_metadata(i, doc or "", dict(meta or {}))
        for i, doc, meta in zip(result["ids"], documents, metadatas, strict=True)
    ]
