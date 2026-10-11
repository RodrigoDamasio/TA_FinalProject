"""SqliteEmbeddingCache (M4): vectors as float32 blobs (384 dims ≈ 1.5 KB), with hit/miss
counters. SQL with bound parameters only. Owner: RAG."""

import threading
from array import array

from . import database

_GET_VECTOR = "SELECT vector FROM embedding_cache WHERE model = ? AND key = ?"
_PUT_VECTOR = "INSERT OR REPLACE INTO embedding_cache (model, key, vector) VALUES (?, ?, ?)"
_COUNT_VECTORS = "SELECT COUNT(*) AS n FROM embedding_cache"


class SqliteEmbeddingCache:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()
        database.init(path)
        self.hits = 0
        self.misses = 0

    def get_many(self, model: str, keys: list[str]) -> dict[str, list[float]]:
        found: dict[str, list[float]] = {}
        with database.connect(self._path) as conn:
            for key in keys:
                row = conn.execute(_GET_VECTOR, (model, key)).fetchone()
                if row is not None:
                    found[key] = array("f", row["vector"]).tolist()
        self.hits += len(found)
        self.misses += len(keys) - len(found)
        return found

    def put_many(self, model: str, items: dict[str, list[float]]) -> None:
        rows = [(model, k, array("f", v).tobytes()) for k, v in items.items()]
        with self._lock, database.connect(self._path) as conn:
            conn.executemany(_PUT_VECTOR, rows)

    def stats(self) -> dict[str, int]:
        with database.connect(self._path) as conn:
            stored = conn.execute(_COUNT_VECTORS).fetchone()["n"]
        return {"stored": stored, "hits": self.hits, "misses": self.misses}
