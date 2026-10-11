"""The Indexer (RAG_PLAN §3.3): normalize → chunk → dedup → embed (cache) → store + BM25,
under ONE lock (a run's tools and the indexing job both call it). Deterministic code, 0 LLM
calls; agents never reach it except through their tools' own run scope. Owner: RAG.

Dedup rule (v1.1 G10): in `run:` scopes a chunk whose content hash is already in the scope IS
that chunk (the same snippet from two engines is stored once); elsewhere only within the same
source_key: deleting one document never removes text another needs, and every run keeps its
own `memory` chunk even when two runs had the same question (deviation from RAG_PLAN §3.3)."""

import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

from app.domain.evidence import EvidenceChunk, chunk_id, content_hash
from app.domain.ports import SourceText, VectorStore

from ..embedding import CachedEmbeddings, Models, ready_embedder
from ..retrieval.bm25 import ScopedBM25
from .chunkers import ChunkOptions, chunk, embed_text
from .normalize import normalize_text

TOOL_WAIT_S = 10.0


def cross_source(scope: str) -> bool:
    return scope.startswith("run:")


class HybridIndexer:
    def __init__(
        self,
        store: VectorStore,
        models: Models,
        embeddings: CachedEmbeddings,
        bm25: ScopedBM25,
        opts: ChunkOptions,
        *,
        wait_s: float = TOOL_WAIT_S,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store, self._models, self._embeddings, self._bm25 = store, models, embeddings, bm25
        self._opts, self._wait, self._clock = opts, wait_s, clock
        self._hashes: dict[str, dict[tuple[str, str], str]] = {}  # scope → (key, hash) → id
        self._lock = threading.RLock()

    # ---- dedup keys ------------------------------------------------------------------------

    def _key(self, scope: str, source_key: str, digest: str) -> tuple[str, str]:
        return ("" if cross_source(scope) else source_key, digest)

    def _scope_hashes(self, scope: str) -> dict[tuple[str, str], str]:
        if scope not in self._hashes:
            self._hashes[scope] = {
                self._key(scope, c.source_key, c.content_hash): c.id
                for c in self._store.iter_chunks(scope)
            }
        return self._hashes[scope]

    # ---- the port -----------------------------------------------------------------------------

    def drafts(self, src: SourceText) -> list[EvidenceChunk]:
        """Chunk one source (no I/O). PDFs arrive normalized, with page offsets."""
        text = src.text if src.pages else normalize_text(src.text)
        now = self._clock().isoformat(timespec="seconds")
        out = []
        for position, d in enumerate(chunk(text, src.title, self._opts, src.pages)):
            body = text[d.start : d.end].strip()
            if not body:
                continue
            digest = content_hash(body)
            out.append(
                EvidenceChunk(
                    chunk_id(src.scope, src.source_key, position, digest),
                    src.scope,
                    src.source_kind,
                    src.source_key,
                    src.title,
                    src.url,
                    src.document_id,
                    src.document_name,
                    d.page,
                    d.heading,
                    position,
                    body,
                    digest,
                    now,
                )  # fmt: skip
            )
        return out

    def index(self, sources: list[SourceText]) -> list[EvidenceChunk]:
        drafted = [c for src in sources for c in self.drafts(src)]
        return self._store_new(drafted)

    def adopt(self, chunks: list[EvidenceChunk], scope: str) -> list[EvidenceChunk]:
        """Copy existing chunks into another scope: same text and metadata, new id; the vector
        comes from the embedding cache (v1.1 G2)."""
        copies = [
            replace(c, id=chunk_id(scope, c.source_key, c.position, c.content_hash), scope=scope)
            for c in chunks
        ]
        return self._store_new(copies)

    def _store_new(self, chunks: list[EvidenceChunk]) -> list[EvidenceChunk]:
        """Store the chunks that are new; return, in input order, each chunk or the stored
        chunk it duplicates."""
        if not chunks:
            return []
        embedder = ready_embedder(self._models, self._wait)
        with self._lock:
            ids: list[str] = []
            new: dict[str, EvidenceChunk] = {}
            for c in chunks:
                hashes = self._scope_hashes(c.scope)
                key = self._key(c.scope, c.source_key, c.content_hash)
                if key not in hashes:
                    hashes[key] = c.id
                    new[c.id] = c
                ids.append(hashes[key])
            if new:
                fresh = list(new.values())
                vectors, _ = self._embeddings.documents(embedder, [embed_text(c) for c in fresh])
                self._store.upsert(fresh, vectors)
                for c in fresh:
                    self._bm25.index(c.scope).add(c.id, embed_text(c), c.source_key, c.position)
            stored = {c.id: c for c in self._store.get(list(dict.fromkeys(ids)))}
            return [stored.get(i) or new[i] for i in ids]

    def remove(self, scope: str, source_key: str | None = None) -> int:
        """Delete from the store and from BM25 together. The dedup map is rebuilt lazily."""
        with self._lock:
            self._hashes.pop(scope, None)
            if source_key is None:
                self._bm25.evict(scope)
                return self._store.delete_scope(scope)
            removed = self._store.delete_source(scope, source_key)
            if self._bm25.loaded(scope):
                self._bm25.index(scope).remove_source(source_key)
            return removed
