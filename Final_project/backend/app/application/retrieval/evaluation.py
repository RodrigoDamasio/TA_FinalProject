"""Retrieval evaluation (RAG_PLAN §3.9, 0 LLM calls). Metrics from M4. A target is a
document plus a heading (path below the document title), pages, or a short quote: none
depends on chunk size, so one dataset scores every chunking setting. Owner: RAG (v1.1 G11)."""

import math
from dataclasses import dataclass
from typing import Any

from app.domain.evidence import EvidenceChunk
from app.domain.ports import Retriever


def precision_at_k(flags: list[bool], k: int) -> float:
    return sum(flags[:k]) / k if k > 0 else 0.0


def recall_at_k(targets_hit: list[int | None], k: int) -> float:
    if not targets_hit:
        return 0.0
    return sum(1 for r in targets_hit if r is not None and r <= k) / len(targets_hit)


def reciprocal_rank(flags: list[bool]) -> float:
    return next((1.0 / (i + 1) for i, f in enumerate(flags) if f), 0.0)


def hit_at_k(flags: list[bool], k: int) -> float:
    return 1.0 if any(flags[:k]) else 0.0


def ndcg_at_k(flags: list[bool], k: int, n_relevant: int) -> float:
    dcg = sum(1 / math.log2(i + 2) for i, f in enumerate(flags[:k]) if f)
    ideal_n = min(k, max(n_relevant, sum(flags[:k])))
    ideal = sum(1 / math.log2(i + 2) for i in range(ideal_n))
    return dcg / ideal if ideal > 0 else 0.0


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def matches(chunk: EvidenceChunk, target: dict[str, Any]) -> bool:
    if chunk.document_name != target["document"]:
        return False
    if "quote" in target:
        return _squash(target["quote"]) in _squash(chunk.text)
    if "pages" in target:
        return chunk.page in target["pages"]
    heading = (chunk.heading or "").split(" > ", 1)
    below_title = heading[1] if len(heading) == 2 else heading[0]
    if below_title.startswith(target["heading"]):
        return True
    # A short section merges forward into the next one (merge_small): the chunk then carries
    # the next heading, but still contains the target's heading line.
    last = target["heading"].split(" > ")[-1]
    return any(
        line.lstrip("#").strip().casefold() == last.casefold()
        for line in chunk.text.splitlines()
        if line.startswith("#")
    )


@dataclass
class Result:
    id: str
    category: str
    hit: float
    mrr: float
    precision: float
    recall: float
    ndcg: float
    top: list[str]


def evaluate(dataset: list[dict[str, Any]], retriever: Retriever, scopes: list[str],
             k: int) -> list[Result]:  # fmt: skip
    out = []
    for item in dataset:
        hits = retriever.search(item["question"], scopes, k)
        chunks = [h.chunk for h in hits]
        targets = item["relevant"]
        flags = [any(matches(c, t) for t in targets) for c in chunks]
        first = [
            next((i + 1 for i, c in enumerate(chunks) if matches(c, t)), None) for t in targets
        ]
        out.append(
            Result(
                item["id"],
                item["category"],
                hit_at_k(flags, k),
                reciprocal_rank(flags),
                precision_at_k(flags, k),
                recall_at_k(first, k),
                ndcg_at_k(flags, k, len(targets)),
                [f"{c.document_name}:{c.page or c.heading or c.position}" for c in chunks[:3]],
            )  # fmt: skip
        )
    return out


def summary(results: list[Result]) -> dict[str, dict[str, float]]:
    """Mean of each metric per category and overall (negatives excluded: nothing to find)."""
    groups: dict[str, list[Result]] = {}
    for r in results:
        if r.category != "negative":
            groups.setdefault(r.category, []).append(r)
            groups.setdefault("all", []).append(r)
    return {
        name: {
            m: round(sum(getattr(r, m) for r in rs) / len(rs), 3)
            for m in ("hit", "mrr", "precision", "recall", "ndcg")
        }
        for name, rs in groups.items()
    }
