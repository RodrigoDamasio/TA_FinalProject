"""Writer: turns the findings and their evidence into the report Markdown (# title, summary,
## sections, [E<n>] citations); on a revision it also sees its previous draft, the review and
the fact check (WRITER_PLAN §3.3–3.4). Owner: WRITER.

One LLM call. The app builds the context, never the model: the evidence numbers the findings,
the previous draft and the fact check cite come first, the rest is ranked by relevance in the
run's own scope, and whole chunks are added until the evidence share of the budget is used.
After the call a structure check (0 calls) adds notices; it never retries."""

import logging
from dataclasses import dataclass

from app.application.citations import cited_numbers
from app.application.context import FIRST_TURN
from app.application.prompts import PromptLibrary
from app.application.report import has_title, parse_report, unwrap_fence
from app.domain.depth import PROFILES, DepthProfile
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.ports import LLMClient, LLMRequest, LLMResponse, UserMessage
from app.domain.transcript import WorkerResult

from .base import WorkerContext, WorkerOutput, clean_citations, text_of, trim, truncation

logger = logging.getLogger(__name__)

WRITER_MAX_EVIDENCE = 20  # evidence blocks per prompt, whatever the budget
MIN_EVIDENCE = 3  # always shown, even over budget
MAX_OUTPUT_TOKENS = 4096

# Shares of ctx.context_chars (WRITER_PLAN §3.3). Unused shares flow to the evidence.
FIRST_DRAFT = {"findings": 0.30}
REVISION = {"findings": 0.15, "draft": 0.35, "feedback": 0.10}


def _usable(ctx: WorkerContext, worker: str) -> WorkerResult | None:
    r = ctx.results.get(worker)
    return r if r is not None and not r.failed else None


def _source(ref: EvidenceRef, chunk: EvidenceChunk) -> str:
    """The `source` attribute of an evidence block: a host, or a document name and page."""
    if ref.source_kind is SourceKind.DOCUMENTS:
        name = ref.document_name or chunk.document_name or ref.title
        return f"{name} p.{ref.page}" if ref.page else name
    url = ref.url or chunk.url or ""
    host = url.split("://", 1)[-1].split("/", 1)[0].removeprefix("www.")
    return host or ref.source_kind.value


@dataclass(frozen=True)
class _Evidence:
    text: str  # the fenced blocks, or "(none)"
    shown: int
    missing: int


class Writer:
    name = "writer"
    title = "Writer"
    description = (
        "writes or revises the sectioned report from the research findings and the evidence "
        "(and from the latest review and fact check, if any), citing [E<n>]"
    )
    needs = frozenset({"researcher", "writer", "reviewer", "fact_checker"})

    def __init__(self, prompts: PromptLibrary) -> None:
        self._prompts = prompts

    # ---- context -------------------------------------------------------------------------

    def _findings(self, ctx: WorkerContext) -> list[WorkerResult]:
        """Every successful Researcher result, oldest first (v1.1 history), latest included."""
        results = [r for r in ctx.history.get("researcher", ()) if not r.failed]
        latest = _usable(ctx, "researcher")
        if latest is not None and latest not in results:
            results.append(latest)
        unique: list[WorkerResult] = []
        for r in results:
            if all(r.content != u.content for u in unique):
                unique.append(r)
        return unique

    def _priority(
        self, ctx: WorkerContext, findings: list[WorkerResult], draft: WorkerResult | None
    ) -> list[int]:
        """Cited numbers first: latest findings, earlier findings, then (revision) the previous
        draft and the fact check. Each in citation order."""
        sources = list(reversed(findings))
        if draft is not None:
            sources.append(draft)
            if (fact := _usable(ctx, "fact_checker")) is not None:
                sources.append(fact)
        order: list[int] = []
        for r in sources:
            for n in [*cited_numbers(r.content), *r.evidence_ns]:
                if n not in order:
                    order.append(n)
        return order

    def _candidates(self, task: str, ctx: WorkerContext, priority: list[int]) -> list[EvidenceRef]:
        issued = {r.n: r for r in ctx.evidence}
        order = [n for n in priority if n in issued]
        if len(order) < WRITER_MAX_EVIDENCE and len(order) < len(issued):
            by_chunk = {r.chunk_id: r.n for r in ctx.evidence}
            try:
                hits = ctx.retriever.search(
                    f"{ctx.question} {task}",
                    scopes=[f"run:{ctx.run_id}"],
                    k=WRITER_MAX_EVIDENCE * 2,
                )
                ranked = [by_chunk[h.chunk.id] for h in hits if h.chunk.id in by_chunk]
            except Exception as err:  # ranking is an optimization: ascending n instead
                logger.info("run=%s writer ranking failed: %s", ctx.run_id, type(err).__name__)
                ranked = []
            for n in [*ranked, *sorted(issued)]:
                if n not in order:
                    order.append(n)
        return [issued[n] for n in order[:WRITER_MAX_EVIDENCE]]

    def _evidence(self, ctx: WorkerContext, refs: list[EvidenceRef], budget: int) -> _Evidence:
        if not refs:
            return _Evidence("(none)", 0, 0)
        loaded = {c.id: c for c in ctx.retriever.get([r.chunk_id for r in refs])}
        blocks: list[str] = []
        used = missing = 0
        for ref in refs:
            chunk = loaded.get(ref.chunk_id)
            if chunk is None:
                missing += 1
                continue
            block = ctx.fence.wrap(
                chunk.text,
                "evidence",
                ref=ref.label,
                source=_source(ref, chunk),
                title=ref.title or chunk.title,
            )
            if len(blocks) >= MIN_EVIDENCE and used + len(block) > budget:
                break
            blocks.append(block)
            used += len(block) + 2
        return _Evidence("\n\n".join(blocks) or "(none)", len(blocks), missing)

    def prompt(self, task: str, ctx: WorkerContext) -> tuple[str, list[tuple[str, str]]]:
        """The user prompt and its notices. Deterministic for identical inputs (cache hits)."""
        f, total = ctx.fence, ctx.context_chars
        draft = _usable(ctx, "writer")
        shares = REVISION if draft is not None else FIRST_DRAFT
        findings = self._findings(ctx)

        def fenced(results: list[WorkerResult], kind: str, share: float) -> str:
            if not results:
                return "(none)"
            each = max(1, int(total * share) // len(results))
            return "\n\n".join(f.wrap(trim(r.content, each), kind) for r in results)

        findings_text = fenced(findings, "research_findings", shares["findings"])
        previous = fenced([draft] if draft else [], "previous_draft", shares.get("draft", 0))
        # Feedback is about the previous draft: without one there is nothing to revise.
        reviewer = _usable(ctx, "reviewer") if draft else None
        checker = _usable(ctx, "fact_checker") if draft else None
        present = len([r for r in (reviewer, checker) if r is not None])
        each = max(1, int(total * shares.get("feedback", 0)) // max(1, present))
        review = f.wrap(trim(reviewer.content, each), "review") if reviewer else "(none)"
        fact = f.wrap(trim(checker.content, each), "fact_check") if checker else "(none)"
        earlier = ctx.earlier_turns or FIRST_TURN  # v1.2: fenced by the Supervisor
        used = sum(len(t) for t in (findings_text, previous, review, fact) if t != "(none)")
        used += len(earlier) if ctx.earlier_turns else 0
        refs = self._candidates(task, ctx, self._priority(ctx, findings, draft))
        evidence = self._evidence(ctx, refs, max(0, total - used))

        profile = PROFILES[ctx.depth]
        user = self._prompts.worker_task(
            "writer_task",
            question=f.wrap(ctx.question, "question"),
            task=task,
            earlier_turns=earlier,
            guidance=depth_block(ctx.depth.value, profile),
            notes=findings_text,
            evidence=evidence.text,
            review=review,
            fact_check=fact,
            previous_draft=previous,
        )
        notices = []
        if evidence.missing:
            notices.append(
                (
                    f"{evidence.missing} evidence chunk(s) are no longer available.",
                    "evidence_missing",
                )
            )
        logger.info(
            "run=%s writer revision=%s findings=%d evidence=%d missing=%d prompt_chars=%d",
            ctx.run_id,
            draft is not None,
            len(findings),
            evidence.shown,
            evidence.missing,
            len(user),
        )
        return user, notices

    # ---- the call ------------------------------------------------------------------------

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        user, notices = self.prompt(task, ctx)
        response = llm.generate(
            LLMRequest(
                system=self._prompts.worker_system(self.name, ctx.canary),
                messages=[UserMessage(user)],
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_budget=0,  # the adapter's floor (LOW on Gemini 3.x)
                model=ctx.model,
            )
        )
        draft = _cut_partial(unwrap_fence(text_of(response)), response)
        notices += truncation(response, self.name)
        draft, cited, unknown = clean_citations(draft, {r.n for r in ctx.evidence})
        if unknown:
            notices.append(
                (f"Removed {len(unknown)} citation(s) to unknown evidence.", "unknown_citation")
            )
        report = parse_report(draft, ctx.evidence, question=ctx.question, depth=ctx.depth)
        notices += structure_notices(
            draft,
            sections=len(report.sections),
            words=report.word_count,
            cited=bool(cited),
            has_evidence=bool(ctx.evidence),
            depth=ctx.depth.value,
            profile=PROFILES[ctx.depth],
        )
        logger.info(
            "run=%s writer words=%d sections=%d cited=%d notices=%d",
            ctx.run_id,
            report.word_count,
            len(report.sections),
            len(cited),
            len(notices),
        )
        data = {"words": report.word_count, "sections": len(report.sections)}
        return WorkerOutput(WorkerResult(self.name, draft, data, cited), notices)


def depth_block(depth: str, profile: DepthProfile) -> str:
    """The depth name, its guidance and the targets (app constants, not fenced)."""
    (w_lo, w_hi), (s_lo, s_hi) = profile.words, profile.sections
    return (
        f"Depth: {depth}. {profile.guidance}\n"
        f"Target: {w_lo}–{w_hi} words in the summary and sections, {s_lo}–{s_hi} sections. "
        f"Aim for about {(w_lo + w_hi) // 2} words: a draft under {w_lo} words is sent back."
    )


def _cut_partial(draft: str, response: LLMResponse) -> str:
    """At the output limit the last paragraph is partial: cut back to the last blank line."""
    if response.finish_reason != "max_tokens":
        return draft
    cut = draft.rstrip().rfind("\n\n")
    return draft[:cut].rstrip() if cut > 0 else draft


def structure_notices(
    draft: str,
    *,
    sections: int,
    words: int,
    cited: bool,
    has_evidence: bool,
    depth: str,
    profile: DepthProfile,
) -> list[tuple[str, str]]:
    """The structure check of WRITER_PLAN §3.4: notices for the Supervisor, never a retry."""
    notices: list[tuple[str, str]] = []
    if not has_title(draft):
        notices.append(("The Writer's draft has no title; the question is used.", "report_format"))
    lo, hi = profile.sections
    if not lo <= sections <= hi:
        noun = "section" if sections == 1 else "sections"
        notices.append(
            (f"The draft has {sections} {noun}; {depth} expects {lo}–{hi}.", "report_format")
        )
    lo, hi = profile.words
    if words < 0.8 * lo or words > 1.2 * hi:
        noun = "word" if words == 1 else "words"
        notices.append(
            (f"The draft has {words:,} {noun}; {depth} expects {lo}–{hi}.", "report_length")
        )
    if has_evidence and not cited:
        notices.append(("The Writer's draft cites no evidence.", "uncited"))
    return notices
