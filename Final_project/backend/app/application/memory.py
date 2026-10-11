"""Memory: what earlier runs found, recalled for the Supervisor (hybrid search, RAG_PLAN §3.8).

Memories whose question equals the current one are excluded, so re-running a question never
changes its own prompt and the response cache stays effective (Lab 3 lesson). Runs of the
current conversation are excluded too (v1.2): the Supervisor already sees them as earlier
turns."""

import re

from app.domain.evidence import SourceKind
from app.domain.ports import Indexer, Memory, MemoryRepository, Retriever, SourceText
from app.domain.run import ResearchRun, now_iso

RECALL_WINDOW = 500
MEMORY_SCOPE = "memory"
MIN_COSINE = 0.55
SUMMARY_CHARS = 600
_MARKDOWN = re.compile(r"[#*_`>|]+|\[E?\d{1,4}\]")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def _key(question: str) -> str:
    return " ".join(question.lower().split())


def summarize(markdown: str, limit: int = SUMMARY_CHARS) -> str:
    text = _LINK.sub(r"\1", markdown)
    text = text.split("### Sources")[0]
    text = " ".join(_MARKDOWN.sub(" ", text).split())
    return text[:limit].rsplit(" ", 1)[0] + "…" if len(text) > limit else text


def memory_from_run(run: ResearchRun) -> Memory:
    """What a completed run leaves behind (public: SUPERVISOR records, RAG decides storage)."""
    if run.report is not None:
        titles = [r.title for r in run.report.references]
        summary = run.report.summary or summarize(run.final_output or "")
    else:
        titles = [r.title for r in run.evidence]
        summary = summarize(run.final_output or "")
    return Memory(run.id, run.question, summary[:SUMMARY_CHARS], titles[:5], now_iso())


class HybridMemoryStore:
    """MemoryStore over the `memory` scope (RAG_PLAN §3.8): each completed run leaves one
    chunk (its question, doubled to weight it, and its summary); recall is hybrid search with
    a relevance floor, so RRF ranks alone never admit noise. Owner: RAG."""

    def __init__(
        self,
        repo: MemoryRepository,
        indexer: Indexer,
        retriever: Retriever,
        *,
        min_cosine: float = MIN_COSINE,
    ) -> None:
        self._repo, self._indexer, self._retriever = repo, indexer, retriever
        self._min_cosine = min_cosine

    def record(self, memory: Memory) -> None:
        self._repo.record(memory)
        text = f"{memory.question}\n{memory.question}\n{memory.summary}"
        self._indexer.index(
            [
                SourceText(
                    MEMORY_SCOPE,
                    SourceKind.MEMORY,
                    f"run:{memory.run_id}",
                    memory.question[:120],
                    text,
                )  # fmt: skip
            ]
        )

    def recall(
        self,
        query: str,
        limit: int,
        exclude_question: str,
        exclude_runs: frozenset[str] = frozenset(),
    ) -> list[Memory]:
        if limit <= 0:
            return []
        by_run = {m.run_id: m for m in self._repo.recent(RECALL_WINDOW)}
        wanted = limit + 10 + len(exclude_runs)
        explain = getattr(self._retriever, "explain", None)
        if explain is not None:
            hits = [
                (e.hit.chunk.source_key, e.bm25_matched or (e.cosine or 0.0) >= self._min_cosine)
                for e in explain(query, [MEMORY_SCOPE], wanted)
            ]
        else:
            hits = [
                (h.chunk.source_key, True)
                for h in self._retriever.search(query, [MEMORY_SCOPE], wanted)
            ]
        excluded = _key(exclude_question)
        out: list[Memory] = []
        newest: dict[str, str] = {}
        for m in by_run.values():  # newest first (recent() order)
            newest.setdefault(_key(m.question), m.run_id)
        for source_key, relevant in hits:
            m = by_run.get(source_key.removeprefix("run:"))
            if m is None or not relevant or m.run_id in exclude_runs:
                continue
            if _key(m.question) == excluded:
                continue
            if newest.get(_key(m.question)) != m.run_id or m in out:
                continue
            out.append(m)
            if len(out) == limit:
                break
        return out

    def delete_older_than(self, cutoff_iso: str) -> int:
        for m in self._repo.recent(RECALL_WINDOW * 20):
            if m.created_at < cutoff_iso:
                self._indexer.remove(MEMORY_SCOPE, f"run:{m.run_id}")
        return self._repo.delete_older_than(cutoff_iso)
