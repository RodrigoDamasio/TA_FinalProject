"""An in-memory VectorStore (brute-force cosine). Used with EMBED_MODE=fake and in tests;
production uses Chroma (RAG). Owner: RAG (BACKEND_PLAN §4)."""

import threading
from collections.abc import Iterator

from app.domain.evidence import EvidenceChunk


def _cosine_distance(a: list[float], b: list[float]) -> float:
    return 1.0 - sum(x * y for x, y in zip(a, b, strict=False))


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: dict[str, tuple[EvidenceChunk, list[float]]] = {}
        self._lock = threading.Lock()

    def upsert(self, chunks: list[EvidenceChunk], vectors: list[list[float]]) -> None:
        with self._lock:
            for chunk, vector in zip(chunks, vectors, strict=True):
                self._rows[chunk.id] = (chunk, vector)

    def query(
        self,
        vector: list[float],
        scopes: list[str],
        n: int,
        source_keys: list[str] | None = None,
    ) -> list[tuple[str, float]]:
        with self._lock:
            rows = [
                (cid, _cosine_distance(vector, v))
                for cid, (c, v) in self._rows.items()
                if c.scope in scopes and (source_keys is None or c.source_key in source_keys)
            ]
        order = {
            cid: (self._rows[cid][0].source_key, self._rows[cid][0].position) for cid, _ in rows
        }
        rows.sort(key=lambda r: (round(r[1], 12), order[r[0]], r[0]))
        return rows[:n]

    def get(self, ids: list[str]) -> list[EvidenceChunk]:
        with self._lock:
            return [self._rows[i][0] for i in ids if i in self._rows]

    def iter_chunks(self, scope: str) -> Iterator[EvidenceChunk]:
        with self._lock:
            chunks = [c for c, _ in self._rows.values() if c.scope == scope]
        yield from sorted(chunks, key=lambda c: (c.source_key, c.position))

    def _delete(self, keep) -> int:
        with self._lock:
            doomed = [cid for cid, (c, _) in self._rows.items() if not keep(c)]
            for cid in doomed:
                del self._rows[cid]
        return len(doomed)

    def delete_scope(self, scope: str) -> int:
        return self._delete(lambda c: c.scope != scope)

    def delete_source(self, scope: str, source_key: str) -> int:
        return self._delete(lambda c: not (c.scope == scope and c.source_key == source_key))

    def count(self, scope: str | None = None) -> int:
        with self._lock:
            return sum(1 for c, _ in self._rows.values() if scope is None or c.scope == scope)
