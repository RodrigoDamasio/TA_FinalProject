"""Embedding with the cache in front (M4): vectors keyed by model + SHA-256 of the exact
text, so identical text (an adopted copy, a re-seeded file) is never embedded twice. Query
vectors use their own key space ('q:'): bge adds an instruction prefix to queries.

`Models` is where the indexer and the retriever get their models: the ONNX implementation
loads in the background, so `embedder()` may raise ModelsLoading. Owner: RAG."""

from typing import Protocol

from app.domain.errors import ModelsLoading
from app.domain.evidence import sha256_hex
from app.domain.ports import Embedder, EmbeddingCache, Reranker


class Models(Protocol):
    def embedder(self) -> Embedder: ...

    def reranker(self) -> tuple[Reranker | None, str | None]: ...

    def wait_ready(self, timeout_s: float) -> bool: ...

    def status(self) -> str: ...


class StaticModels:
    """Models that are ready at once (EMBED_MODE=fake, tests)."""

    def __init__(self, embedder: Embedder, reranker: Reranker | None = None) -> None:
        self._embedder, self._reranker = embedder, reranker

    def embedder(self) -> Embedder:
        return self._embedder

    def reranker(self) -> tuple[Reranker | None, str | None]:
        return (self._reranker, None) if self._reranker else (None, "disabled")

    def wait_ready(self, timeout_s: float) -> bool:
        return True

    def status(self) -> str:
        return "ready"


def ready_embedder(models: Models, timeout_s: float) -> Embedder:
    if not models.wait_ready(timeout_s):
        raise ModelsLoading()
    return models.embedder()


class CachedEmbeddings:
    def __init__(self, cache: EmbeddingCache) -> None:
        self._cache = cache

    def documents(self, embedder: Embedder, texts: list[str]) -> tuple[list[list[float]], int]:
        """(vectors in input order, number of cache hits)."""
        keys = [sha256_hex(t) for t in texts]
        found = self._cache.get_many(embedder.name, list(dict.fromkeys(keys)))
        missing = [i for i, k in enumerate(keys) if k not in found]
        unique_missing = list(dict.fromkeys(keys[i] for i in missing))
        if unique_missing:
            first_text = {keys[i]: texts[i] for i in missing}
            vectors = embedder.embed_documents([first_text[k] for k in unique_missing])
            new = dict(zip(unique_missing, vectors, strict=True))
            self._cache.put_many(embedder.name, new)
            found |= new
        return [found[k] for k in keys], len(texts) - len(missing)

    def query(self, embedder: Embedder, text: str) -> list[float]:
        key = sha256_hex("q:" + text)
        found = self._cache.get_many(embedder.name, [key])
        if key in found:
            return found[key]
        vector = embedder.embed_query(text)
        self._cache.put_many(embedder.name, {key: vector})
        return vector
