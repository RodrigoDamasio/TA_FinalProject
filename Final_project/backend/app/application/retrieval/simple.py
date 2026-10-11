"""Step 3 STUB of the Retriever port: vector search only. RAG replaces it with the hybrid
retriever (vector + BM25 per scope + RRF, RAG_PLAN §3.4). Owner: RAG."""

from app.domain.evidence import EvidenceChunk
from app.domain.ports import Embedder, RetrievedChunk, VectorStore


class SimpleRetriever:
    def __init__(self, store: VectorStore, embedder: Embedder) -> None:
        self._store, self._embedder = store, embedder

    def search(
        self, query: str, scopes: list[str], k: int, source_keys: list[str] | None = None
    ) -> list[RetrievedChunk]:
        if k <= 0 or not scopes:
            return []
        hits = self._store.query(self._embedder.embed_query(query), scopes, k, source_keys)
        chunks = {c.id: c for c in self._store.get([cid for cid, _ in hits])}
        return [RetrievedChunk(chunks[cid], 1.0 - d) for cid, d in hits if cid in chunks]

    def get(self, chunk_ids: list[str]) -> list[EvidenceChunk]:
        return self._store.get(chunk_ids)
