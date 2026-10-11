"""Writer: turns the findings and their evidence into the report Markdown (# title, summary,
## sections, [E<n>] citations); on a revision it also sees its previous draft, the review and
the fact check. Step 3 version: WRITER_PLAN §3.3 adds the evidence ranking and budgets.
Owner: WRITER."""

from app.application.prompts import PromptLibrary
from app.domain.depth import PROFILES
from app.domain.ports import LLMClient, LLMRequest, UserMessage
from app.domain.transcript import WorkerResult

from .base import WorkerContext, WorkerOutput, clean_citations, text_of, trim, truncation


class Writer:
    name = "writer"
    title = "Writer"
    description = (
        "writes or revises the report from the research findings and evidence (and from the "
        "latest review and fact check, if any), citing evidence as [E<n>]"
    )
    needs = frozenset({"researcher", "writer", "reviewer", "fact_checker"})

    def __init__(self, prompts: PromptLibrary) -> None:
        self._prompts = prompts

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        f, budget = ctx.fence, ctx.context_chars // 3

        def block(worker: str, kind: str) -> str:
            r = ctx.results.get(worker)
            return f.wrap(trim(r.content, budget), kind) if r else "(none)"

        findings = ctx.results.get("researcher")
        wanted = [r for r in ctx.evidence if findings and r.n in findings.evidence_ns]
        wanted += [r for r in ctx.evidence if r not in wanted][: max(0, 12 - len(wanted))]
        texts = {c.id: c.text for c in ctx.retriever.get([r.chunk_id for r in wanted])}
        evidence = "\n\n".join(
            f.wrap(trim(texts[r.chunk_id], 1200), "evidence", ref=r.label, title=r.title)
            for r in wanted
            if r.chunk_id in texts
        )
        user = self._prompts.worker_task(
            "writer_task",
            question=f.wrap(ctx.question, "question"),
            task=task,
            notes=block("researcher", "research_findings"),
            evidence=evidence or "(none)",
            guidance=PROFILES[ctx.depth].guidance,
            review=block("reviewer", "review"),
            fact_check=block("fact_checker", "fact_check"),
            previous_draft=block("writer", "previous_draft"),
        )
        response = llm.generate(
            LLMRequest(
                system=self._prompts.worker_system(self.name, ctx.canary),
                messages=[UserMessage(user)],
                max_output_tokens=4096,
                thinking_budget=0,
                model=ctx.model,
            )
        )
        draft, cited, unknown = clean_citations(text_of(response), {r.n for r in ctx.evidence})
        notices = truncation(response, self.name)
        if unknown:
            notices.append(
                (f"Removed {len(unknown)} citation(s) to unknown evidence.", "unknown_citation")
            )
        return WorkerOutput(WorkerResult(self.name, draft, {}, cited), notices)
