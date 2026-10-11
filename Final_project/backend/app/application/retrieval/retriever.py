"""HybridRetriever (RAG_PLAN §3.4): vector + BM25, fused with weighted RRF.

Two RRF stages: each scope's BM25 list is ranked with that scope's own statistics and fused
first (equal weights), then fused with the vector list (equal weights, see WEIGHTS). Scores from
different scopes are never compared or summed, which fixes M4's cross-codebase leak.
Optional cross-encoder rerank (local evaluation only). An optional per-source cap exists but is
OFF by default: the evaluation showed it costs recall with only 6 evidence slots. Ties break
on (source_key, position), never on the run-scoped chunk id, so identical runs rank alike
(stable LLM cache keys). Owner: RAG."""

from dataclasses import dataclass

from app.domain.evidence import EvidenceChunk
from app.domain.ports import RetrievedChunk, VectorStore

from ..embedding import CachedEmbeddings, Models
from .bm25 import ScopedBM25
from .fusion import rrf

# Equal weights (standard RRF), not M4's 0.7/0.3: with 0.7/0.3 a passage that BM25 ranks
# first but the vector list misses scores below the 20th vector hit, so exact identifiers
# (header names, error codes, markers) could never surface. Deviation from RAG_PLAN §3.4,
# to be confirmed by the retrieval evaluation grid.
WEIGHTS = {"vector": 1.0, "bm25": 1.0}
PER_SOURCE: int | None = None  # RAG_PLAN said 3; the evaluation grid showed it hurts (§Deviations)
RERANK_TOP = 20


@dataclass(frozen=True)
class Explained:
    """A hit with the evidence for the memory relevance floor (RAG_PLAN §3.8)."""

    hit: RetrievedChunk
    bm25_matched: bool
    cosine: float | None


class HybridRetriever:
    def __init__(
        self,
        store: VectorStore,
        models: Models,
        embeddings: CachedEmbeddings,
        bm25: ScopedBM25,
        *,
        mode: str = "hybrid",  # vector | bm25 | hybrid | hybrid_rerank (evaluation grid)
        per_source: int | None = PER_SOURCE,
    ) -> None:
        self._store, self._models, self._embeddings, self._bm25 = store, models, embeddings, bm25
        self.mode, self.per_source = mode, per_source

    def search(
        self, query: str, scopes: list[str], k: int, source_keys: list[str] | None = None
    ) -> list[RetrievedChunk]:
        return [e.hit for e in self.explain(query, scopes, k, source_keys)]

    def explain(
        self, query: str, scopes: list[str], k: int, source_keys: list[str] | None = None
    ) -> list[Explained]:
        q = query.strip()[:500]
        if not q or not scopes or k <= 0:
            return []
        n = max(20, 3 * k)
        lists: dict[str, list[str]] = {}
        cosine: dict[str, float] = {}
        keyword_ids: set[str] = set()
        if self.mode != "bm25":
            vector = self._embeddings.query(self._models.embedder(), q)
            hits = self._store.query(vector, scopes, n, source_keys)
            lists["vector"] = [cid for cid, _ in hits]
            cosine = {cid: 1.0 - d for cid, d in hits}
        if self.mode != "vector":
            per_scope = {
                s: [h.chunk_id for h in self._bm25.search(s, q, n, source_keys)] for s in scopes
            }
            keyword_ids = {cid for ids in per_scope.values() for cid in ids}
            lists["bm25"] = [f.chunk_id for f in rrf(per_scope)][:n]
        fused = rrf(lists, weights=WEIGHTS)[:n]
        chunks = {c.id: c for c in self._store.get([f.chunk_id for f in fused])}
        ranked = [(chunks[f.chunk_id], f.score) for f in fused if f.chunk_id in chunks]
        # run-independent tie-breaks (ids contain the run scope): identical runs rank alike
        ranked.sort(key=lambda x: (-x[1], x[0].source_key, x[0].position))
        ranked = self._rerank(q, ranked)
        out: list[Explained] = []
        per_source: dict[str, int] = {}
        for c, score in ranked:
            if source_keys is None and self.per_source is not None:
                if per_source.get(c.source_key, 0) >= self.per_source:
                    continue
                per_source[c.source_key] = per_source.get(c.source_key, 0) + 1
            out.append(Explained(RetrievedChunk(c, score), c.id in keyword_ids, cosine.get(c.id)))
            if len(out) == k:
                break
        return out

    def _rerank(
        self, query: str, ranked: list[tuple[EvidenceChunk, float]]
    ) -> list[tuple[EvidenceChunk, float]]:
        if self.mode not in ("hybrid", "hybrid_rerank") or not ranked:
            return ranked
        reranker, _ = self._models.reranker()
        if reranker is None:
            return ranked
        head, tail = ranked[:RERANK_TOP], ranked[RERANK_TOP:]
        scores = reranker.score(query, [c.text for c, _ in head])
        reordered = sorted(zip(head, scores, strict=True), key=lambda x: (-x[1], x[0][0].id))
        return [(c, float(s)) for (c, _), s in reordered] + tail

    def get(self, chunk_ids: list[str]) -> list[EvidenceChunk]:
        return self._store.get(chunk_ids)
