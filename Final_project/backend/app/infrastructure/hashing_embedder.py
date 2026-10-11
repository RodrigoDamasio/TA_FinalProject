"""EMBED_MODE=fake: a deterministic bag-of-words hashing embedder (M4 idea). No model, no
download; similar texts share buckets, so retrieval still ranks sensibly in tests and E2E.
Owner: RAG (BACKEND_PLAN §4)."""

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    name = "fake-hashing-256"
    dim = 256
    max_tokens = 512

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for token in _TOKEN.findall(text.lower()):
            h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")
            v[h % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)
