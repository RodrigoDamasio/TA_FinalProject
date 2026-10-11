"""Fact-Checker: checks each cited sentence of the latest draft against the text of the
evidence it cites (structured JSON). Step 3 version: REVIEWER_PLAN §3 redesigns it around
retrieval per claim and `needs_revision`. Owner: REVIEWER."""

import re

from app.application.citations import cited_numbers
from app.application.prompts import PromptLibrary
from app.application.structured import StructuredCaller
from app.application.tracing import Tracer
from app.domain.ports import LLMClient
from app.domain.schemas import FactCheck, FactCheckLLM
from app.domain.transcript import WorkerResult

from .base import WorkerContext, WorkerOutput

MAX_SENTENCES = 12
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")


def cited_sentences(draft: str, limit: int = MAX_SENTENCES) -> list[tuple[str, list[int]]]:
    out = []
    for piece in _SENTENCE_END.split(draft):
        sentence = piece.strip(" -*#>\t")
        numbers = cited_numbers(sentence)
        if numbers and len(sentence) > 15:
            out.append((sentence, sorted(set(numbers))))
        if len(out) == limit:
            break
    return out


def render_fact_check(check: FactCheck) -> str:
    counts = {
        s: sum(1 for c in check.claims if c.status == s)
        for s in ("supported", "unsupported", "contradicted")
    }
    lines = ["Fact check: " + ", ".join(f"{n} {status}" for status, n in counts.items()) + "."]
    for c in check.claims:
        if c.status != "supported":
            note = f" ({c.note})" if c.note else ""
            lines.append(f'- {c.status}: "{c.sentence}"{note}')
    return "\n".join(lines)


class FactChecker:
    name = "fact_checker"
    title = "Fact-Checker"
    description = (
        "checks each cited sentence of the latest draft against the evidence it cites; "
        "can run in parallel with the reviewer"
    )
    needs = frozenset({"writer"})

    def __init__(self, prompts: PromptLibrary) -> None:
        self._prompts = prompts

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        draft = ctx.results.get("writer")
        sentences = cited_sentences(draft.content) if draft else []
        if not sentences:
            text = "There is no draft with cited sentences to check yet."
            return WorkerOutput(WorkerResult(self.name, text, failed=True))
        cited = sorted({n for _, ns in sentences for n in ns})
        by_n = {r.n: r for r in ctx.evidence if r.n in cited}
        texts = {c.id: c.text for c in ctx.retriever.get([r.chunk_id for r in by_n.values()])}
        per_source = max(1000, ctx.context_chars // max(1, len(by_n)))
        f = ctx.fence
        listed = "\n".join(
            f"{i}. [cites {', '.join(map(str, ns))}] {s}" for i, (s, ns) in enumerate(sentences, 1)
        )
        sources = (
            "\n\n".join(
                f.wrap(texts[r.chunk_id][:per_source], "evidence", ref=r.label, title=r.title)
                for r in by_n.values()
                if r.chunk_id in texts
            )
            or "(the cited evidence is not available)"
        )
        user = self._prompts.worker_task(
            "fact_checker_task",
            task=task,
            sentences=f.wrap(listed, "draft_sentences"),
            sources=sources,
        )
        result = StructuredCaller(llm, self._prompts, ctx.model or "").call(
            self._prompts.worker_system(self.name, ctx.canary),
            user,
            FactCheckLLM,
            FactCheck,
            Tracer("worker.fact_checker", ctx.run_id),
            max_output_tokens=3072,
        )
        check = result.value or result.last_valid
        assert check is not None  # noqa: S101 — call() raises when nothing was valid
        cited_ns = tuple(sorted({c.source_n for c in check.claims if c.source_n in by_n}))
        return WorkerOutput(
            WorkerResult(
                self.name, render_fact_check(check), check.model_dump(mode="json"), cited_ns
            )
        )
