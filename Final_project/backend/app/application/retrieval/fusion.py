"""Weighted Reciprocal Rank Fusion: merge ranked lists by rank, not score.

Vector similarities (0..1) and BM25 scores (unbounded) live on different scales; RRF
(Cormack et al. 2009) needs no normalization. Each list has a weight — the course's
hybrid search (§3.3) uses 0.7 for vectors and 0.3 for BM25. A document missing from a
list contributes nothing for it. Copied as-is from M4. Owner: RAG."""

from dataclasses import dataclass

RRF_K = 60


@dataclass(frozen=True)
class Fused:
    chunk_id: str
    score: float
    ranks: dict[str, int]  # list name → 1-based rank in that list


def rrf(
    lists: dict[str, list[str]], k: int = RRF_K, weights: dict[str, float] | None = None
) -> list[Fused]:
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ids in lists.items():
        weight = (weights or {}).get(name, 1.0)
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + weight / (k + rank)
            ranks.setdefault(chunk_id, {})[name] = rank
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], min(ranks[kv[0]].values()), kv[0]))
    return [Fused(i, s, ranks[i]) for i, s in ordered]
