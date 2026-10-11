"""Test doubles (BACKEND_PLAN §19.2, Shared): a scriptable fake LLM built on the demo client
(which already plays every role from the real prompts), plus the fakes of every port.
Extend this file only by ADDING fakes; never change an existing one's behavior."""

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.evidence import EvidenceChunk
from app.domain.ports import (
    LLMRequest,
    LLMResponse,
    RetrievedChunk,
    SendResult,
    TokenUsage,
    UserMessage,
)
from app.infrastructure.demo import DemoLLMClient
from app.infrastructure.fake_search import FakePageFetcher, FakeWebSearch  # noqa: F401
from app.infrastructure.fake_telegram import FakeTelegram  # noqa: F401
from app.infrastructure.hashing_embedder import HashingEmbedder  # noqa: F401
from app.infrastructure.memory_vector_store import InMemoryVectorStore  # noqa: F401

USAGE = TokenUsage(input=100, output=50)
ROLES = ("supervisor", "researcher", "writer", "reviewer", "fact_checker")
Reply = str | dict | Callable[[str], Any] | Exception


def role_of(request: LLMRequest) -> str:
    if request.response_schema is not None:
        return "reviewer" if request.response_schema.__name__ == "ReviewLLM" else "fact_checker"
    for role in ("Supervisor", "Researcher", "Writer"):
        if f"You are the {role}" in request.system:
            return role.lower()
    return "other"


class FakeLLM(DemoLLMClient):
    """Demo answers by default.

    script:  {role: [reply, ...]} consumed in order (str for text roles, dict for the
             structured ones, a callable prompt → reply, or an exception to raise);
             when a role's list is empty the demo answer is used
    fail:    {role | "any": exception} raised on every call of that role
    delay:   {role: seconds} (to observe parallel workers)
    """

    def __init__(self, **options: Any) -> None:
        super().__init__()
        self.script: dict[str, list[Reply]] = {
            k: list(v) for k, v in options.get("script", {}).items()
        }
        self.fail: dict[str, Exception] = dict(options.get("fail", {}))
        self.delay: dict[str, float] = dict(options.get("delay", {}))
        self.requests: list[tuple[str, LLMRequest]] = []
        self.windows: list[tuple[str, float, float]] = []  # role, start, end
        self._lock = threading.Lock()

    def generate(self, request: LLMRequest) -> LLMResponse:
        role = role_of(request)
        with self._lock:
            self.requests.append((role, request))
            scripted = self.script.get(role)
            item = scripted.pop(0) if scripted else None
        start = time.monotonic()
        if role in self.delay:
            time.sleep(self.delay[role])
        try:
            error = self.fail.get(role) or self.fail.get("any")
            if error is not None:
                raise error
            if isinstance(item, Exception):
                raise item
            prompt = "\n".join(m.text for m in request.messages if isinstance(m, UserMessage))
            value = item(prompt) if callable(item) else item
            if value is None:
                response = super().generate(request)
                return LLMResponse(
                    response.text, response.tool_calls, response.parsed, USAGE, "stop"
                )
            if isinstance(value, LLMResponse):
                return value
            if isinstance(value, dict):
                return LLMResponse(json.dumps(value), [], value, USAGE, "stop")
            return LLMResponse(value, [], None, USAGE, "stop")
        finally:
            with self._lock:
                self.windows.append((role, start, time.monotonic()))

    def calls(self, role: str | None = None) -> int:
        return sum(1 for r, _ in self.requests if role is None or r == role)

    def prompts(self, role: str) -> list[str]:
        return [
            "\n".join(m.text for m in req.messages if isinstance(m, UserMessage))
            for r, req in self.requests
            if r == role
        ]

    def systems(self, role: str) -> list[str]:
        return [req.system for r, req in self.requests if r == role]


RAG_TASK = "Find facts about retrieval-augmented generation and fine-tuning."


def delegate(
    worker: str,
    task: str = RAG_TASK,
    reason: str = "Next.",
) -> str:
    return f"REASON: {reason}\nDELEGATE: {worker}\nTASK: {task}"


def final(text: str = "## Answer\n\nThe answer [1].", reason: str = "Done.") -> str:
    return f"REASON: {reason}\nFINAL:\n{text}"


def review(verdict: str = "approve", issues: list | None = None, flags: list | None = None):
    return {"verdict": verdict, "issues": issues or [], "balance_flags": flags or []}


# ---- §19.2 fakes ------------------------------------------------------------------------------


class ScriptedLLM:
    """Returns queued LLMResponse objects in order (text, tool calls, finish reasons) and
    records every request, so tests can assert offered tools and prompt shapes."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("ScriptedLLM: no response left")
        return self.responses.pop(0)


def text_response(text: str, finish: str = "stop") -> LLMResponse:
    return LLMResponse(text, [], None, USAGE, finish)  # type: ignore[arg-type]


class FakeRetriever:
    """Retriever over a dict of chunks: `search` returns them in a set order (or raises)."""

    def __init__(self, chunks: list[EvidenceChunk] | None = None, fail: bool = False) -> None:
        self.chunks = {c.id: c for c in chunks or []}
        self.fail = fail
        self.searches: list[tuple[str, list[str], int]] = []

    def search(
        self, query: str, scopes: list[str], k: int, source_keys: list[str] | None = None
    ) -> list[RetrievedChunk]:
        self.searches.append((query, scopes, k))
        if self.fail:
            raise RuntimeError("retrieval failed")
        hits = [
            c
            for c in self.chunks.values()
            if c.scope in scopes and (source_keys is None or c.source_key in source_keys)
        ]
        return [RetrievedChunk(c, 1.0 - i / 100) for i, c in enumerate(hits[:k])]

    def get(self, chunk_ids: list[str]) -> list[EvidenceChunk]:
        return [self.chunks[i] for i in chunk_ids if i in self.chunks]


class FakeWebhookSender:
    """Records POSTs; `results` (consumed in order) or `result` decide the outcome."""

    def __init__(self, result: SendResult | None = None, results: list | None = None) -> None:
        self.result = result or SendResult(True, 200)
        self.results = list(results or [])
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        self.posts.append((url, body, dict(headers)))
        return self.results.pop(0) if self.results else self.result


class FakeClock:
    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class DictEmbeddingCache:
    """EmbeddingCache in memory."""

    def __init__(self) -> None:
        self.vectors: dict[tuple[str, str], list[float]] = {}

    def get_many(self, model: str, keys: list[str]) -> dict[str, list[float]]:
        return {k: self.vectors[(model, k)] for k in keys if (model, k) in self.vectors}

    def put_many(self, model: str, items: dict[str, list[float]]) -> None:
        self.vectors.update({(model, k): v for k, v in items.items()})

    def stats(self) -> dict[str, int]:
        return {"stored": len(self.vectors)}


def rag_stack(max_chars: int = 400, min_chars: int = 100):
    """The real hybrid RAG stack over the fakes: (indexer, retriever, store, bm25)."""
    from app.application.embedding import CachedEmbeddings, StaticModels
    from app.application.indexing.chunkers import ChunkOptions, embed_text
    from app.application.indexing.indexer import HybridIndexer
    from app.application.retrieval.bm25 import ScopedBM25
    from app.application.retrieval.retriever import HybridRetriever

    store = InMemoryVectorStore()
    models = StaticModels(HashingEmbedder())
    embeddings = CachedEmbeddings(DictEmbeddingCache())
    bm25 = ScopedBM25(
        lambda scope: (
            (c.id, embed_text(c), c.source_key, c.position) for c in store.iter_chunks(scope)
        )
    )
    indexer = HybridIndexer(
        store, models, embeddings, bm25, ChunkOptions(max_chars, min_chars, 0.15)
    )
    return indexer, HybridRetriever(store, models, embeddings, bm25), store, bm25
