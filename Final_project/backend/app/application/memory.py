"""Memory (extension): what earlier runs found, recalled for the Supervisor with BM25 (Lab 4).

Memories whose question equals the current one are excluded, so re-running a question never
changes its own prompt and the response cache stays effective (Lab 3 lesson)."""

import re

from app.domain.ports import Memory, MemoryRepository
from app.domain.run import ResearchRun, now_iso

from .retrieval.bm25 import BM25Index

RECALL_WINDOW = 500
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


class BM25Memory:
    """MemoryStore over a MemoryRepository."""

    def __init__(self, repo: MemoryRepository) -> None:
        self._repo = repo

    def record(self, memory: Memory) -> None:
        self._repo.record(memory)

    def recall(self, query: str, limit: int, exclude_question: str) -> list[Memory]:
        excluded = _key(exclude_question)
        newest_per_question: dict[str, Memory] = {}
        for m in self._repo.recent(RECALL_WINDOW):  # newest first
            if _key(m.question) != excluded:
                newest_per_question.setdefault(_key(m.question), m)
        candidates = {m.run_id: m for m in newest_per_question.values()}
        if not candidates or limit <= 0:
            return []
        index = BM25Index()
        for m in candidates.values():
            index.add(m.run_id, "memory", f"{m.question} {m.question} {m.summary}")
        hits = index.search(query, ["memory"], limit)
        return [candidates[h.chunk_id] for h in hits if h.score > 0]
