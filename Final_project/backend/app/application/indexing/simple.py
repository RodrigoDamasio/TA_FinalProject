"""Step 3 STUB of the Indexer port: paragraph chunks, no Markdown/PDF awareness, no BM25.
RAG replaces it with the M4-based indexer (RAG_PLAN §3.3). Owner: RAG."""

import re
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.evidence import EvidenceChunk, chunk_id, content_hash
from app.domain.ports import Embedder, SourceText, VectorStore

_PARAGRAPHS = re.compile(r"\n\s*\n")


def split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Pack paragraphs into chunks of at most max_chars (a long paragraph is cut)."""
    chunks: list[str] = []
    current = ""
    for para in (p.strip() for p in _PARAGRAPHS.split(text)):
        if not para:
            continue
        while len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:max_chars])
            para = para[max_chars:]
        if current and len(current) + 2 + len(para) > max_chars:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


class SimpleIndexer:
    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        max_chars: int = 1400,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store, self._embedder, self._max = store, embedder, max_chars
        self._clock = clock
        self._lock = threading.Lock()

    def index(self, sources: list[SourceText]) -> list[EvidenceChunk]:
        now = self._clock().isoformat(timespec="seconds")
        chunks: list[EvidenceChunk] = []
        for src in sources:
            for position, text in enumerate(split_paragraphs(src.text, self._max)):
                digest = content_hash(text)
                chunks.append(
                    EvidenceChunk(
                        chunk_id(src.scope, src.source_key, position, digest),
                        src.scope,
                        src.source_kind,
                        src.source_key,
                        src.title,
                        src.url,
                        src.document_id,
                        src.document_name,
                        None,
                        None,
                        position,
                        text,
                        digest,
                        now,
                    )
                )
        if chunks:
            with self._lock:
                vectors = self._embedder.embed_documents([f"{c.title}\n{c.text}" for c in chunks])
                self._store.upsert(chunks, vectors)
        return chunks

    def adopt(self, chunks: list[EvidenceChunk], scope: str) -> list[EvidenceChunk]:
        copies = [
            replace(c, id=chunk_id(scope, c.source_key, c.position, c.content_hash), scope=scope)
            for c in chunks
        ]
        if copies:
            with self._lock:
                vectors = self._embedder.embed_documents([f"{c.title}\n{c.text}" for c in copies])
                self._store.upsert(copies, vectors)
        return copies

    def remove(self, scope: str, source_key: str | None = None) -> int:
        if source_key is None:
            return self._store.delete_scope(scope)
        return self._store.delete_source(scope, source_key)
