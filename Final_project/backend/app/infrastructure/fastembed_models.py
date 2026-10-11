"""Local ONNX models through fastembed (§2.1, §8.3) — no PyTorch, 0 API calls.

Embedder: BAAI/bge-small-en-v1.5 (384 dims). Reranker: Xenova/ms-marco-MiniLM-L-6-v2
cross-encoder. Both load once, in a background thread, so the server answers /health at
once; until the embedder is ready, search raises ModelsLoading (503) and the reranker is
skipped with reason "loading". Batch size 4 keeps peak memory near 310 MB (measured).
Copied from M4 (Railway: EMBED_BATCH_SIZE=1, ONNX_THREADS=1, RERANK_MODEL=""). Owner: RAG."""

import logging
import threading
from typing import Any

from app.domain.errors import ModelsLoading
from app.domain.ports import Embedder, Reranker

logger = logging.getLogger(__name__)
# Context window of the supported embedders (tokens); used by the token-budget test.
MAX_TOKENS = {"BAAI/bge-small-en-v1.5": 512, "sentence-transformers/all-MiniLM-L6-v2": 256}


class FastEmbedEmbedder:
    def __init__(self, model: Any, name: str, batch_size: int) -> None:
        self._model, self.name, self._batch = model, name, batch_size
        self.dim = len(next(iter(model.query_embed(["dimension probe"]))))
        self.max_tokens = MAX_TOKENS.get(name, 512)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [v.tolist() for v in self._model.embed(texts, batch_size=self._batch)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed([text]))).tolist()

    def count_tokens(self, text: str) -> int:
        """Exact token count with the model's own tokenizer (for the 512-window check)."""
        return len(self._model.model.tokenizer.encode(text).ids)


class FastEmbedReranker:
    def __init__(self, model: Any, name: str) -> None:
        self._model, self.name = model, name

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self._model.rerank(query, texts, batch_size=len(texts))]


def load_embedder(name: str, cache_dir: str, threads: int, batch_size: int) -> FastEmbedEmbedder:
    from fastembed import TextEmbedding

    model = TextEmbedding(name, cache_dir=cache_dir, threads=threads)
    return FastEmbedEmbedder(model, name, batch_size)


def load_reranker(name: str, cache_dir: str, threads: int) -> FastEmbedReranker:
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return FastEmbedReranker(TextCrossEncoder(name, cache_dir=cache_dir, threads=threads), name)


class OnnxModels:
    def __init__(
        self,
        embed_model: str,
        rerank_model: str,
        cache_dir: str,
        threads: int,
        batch_size: int,
        loader=None,
    ) -> None:
        self._names = (embed_model, rerank_model)
        self._cache_dir, self._threads, self._batch = cache_dir, threads, batch_size
        self._load = loader or self._load_real
        self._embedder: Embedder | None = None
        self._reranker: Reranker | None = None
        self._embedder_ready = threading.Event()
        self._done = threading.Event()
        self._error: str | None = None

    def start(self) -> None:
        threading.Thread(target=self._run, name="model-loader", daemon=True).start()

    def _load_real(self, kind: str) -> Embedder | Reranker:
        embed_model, rerank_model = self._names
        if kind == "embedder":
            return load_embedder(embed_model, self._cache_dir, self._threads, self._batch)
        return load_reranker(rerank_model, self._cache_dir, self._threads)

    def _run(self) -> None:
        try:
            self._embedder = self._load("embedder")  # type: ignore[assignment]
            self._embedder_ready.set()
            logger.info("embedder ready model=%s", self._names[0])
            if self._names[1]:
                self._reranker = self._load("reranker")  # type: ignore[assignment]
                logger.info("reranker ready model=%s", self._names[1])
        except Exception as err:  # a missing model must not kill the server
            self._error = type(err).__name__
            logger.exception("model loading failed")
        finally:
            self._done.set()

    def embedder(self) -> Embedder:
        if self._embedder is None:
            raise ModelsLoading()
        return self._embedder

    def reranker(self) -> tuple[Reranker | None, str | None]:
        if not self._names[1]:
            return None, "disabled"
        if self._reranker is None:
            return None, "loading" if not self._done.is_set() else "unavailable"
        return self._reranker, None

    def wait_ready(self, timeout_s: float) -> bool:
        return self._embedder_ready.wait(timeout_s)

    def wait_all(self, timeout_s: float) -> bool:
        """Both models attempted (the CLI waits for the reranker too)."""
        return self._done.wait(timeout_s)

    def status(self) -> str:
        if self._error and self._embedder is None:
            return "failed"
        return "ready" if self._embedder is not None else "loading"
