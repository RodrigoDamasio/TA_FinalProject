"""The supervisor loop (SUPERVISOR_PLAN §3): each iteration the Supervisor decides — via the
DELEGATE/TASK/FINAL protocol — which worker acts next, or delivers the report (FINAL = the full
report Markdown, turned into a `Report` by `parse_report`).

Guards: one repair call for an invalid reply or a broken order rule; an iteration limit and a
per-run call budget (`3 × iterations left + 5`), both ending in a forced FINAL call that the
workers can never spend (`ReserveGuard`); and a deterministic fallback (latest Writer draft,
then the Researcher's findings) when even that fails or the quota runs out. The loop can never
spin. Owner: SUPERVISOR."""

import logging
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from app.domain.errors import (
    BudgetExceeded,
    CanaryLeak,
    LLMBadResponse,
    LLMError,
    LLMRequestRejected,
    ProtocolError,
)
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.ports import (
    AssistantMessage,
    LLMClient,
    LLMRequest,
    LLMResponse,
    MemoryStore,
    Retriever,
    RunRepository,
    UserMessage,
)
from app.domain.protocol import parse_decision
from app.domain.run import ResearchRun, RunStatus
from app.domain.transcript import CallMeta, Decision, Delegation, ToolCallRecord, WorkerResult

from .budget import CallBudget
from .citations import cited_numbers, clean_citations
from .context import (
    FIRST_TURN,
    budget_line,
    conversation_context,
    depth_block,
    earlier_turns,
    evidence_block,
    last_hint,
    memory_block,
    sources_block,
    transcript_view,
)
from .memory import memory_from_run
from .prompts import PromptLibrary
from .report import parse_report, snippets_for
from .security import DataFence, OutputValidator, canary_for
from .tools.base import ToolContext, ToolMemo, ToolRegistry
from .tools.conversation import MAX_SCOPES
from .workers.base import StepCounter, WorkerContext, WorkerOutput, WorkerRegistry

logger = logging.getLogger("app.supervisor")

RESERVE = 1  # LLM calls kept for the forced FINAL
REVIEWERS = frozenset({"reviewer", "fact_checker"})
ORDER_TOGETHER = (
    "The reviewer and fact_checker check the writer's draft, so delegate them after the "
    "writer has answered."
)
ORDER_NO_DRAFT = "There is no draft yet: delegate the writer first."
ORDER_NO_EVIDENCE = "Delegate the researcher first: there is no evidence yet."
BUDGET_STOP = "Worker stopped: the run's LLM budget is used up."
_TITLE = re.compile(r"^# \S", re.MULTILINE)
_HEADING = re.compile(r"^#{1,2} ", re.MULTILINE)


class ReserveGuard:
    """The workers' client: refuses a call (BudgetExceeded) when only the reserve is left, so
    the last call always stays for the forced FINAL. The Supervisor uses the bare client."""

    def __init__(self, inner: LLMClient, budget: CallBudget, reserve: int = RESERVE) -> None:
        self._inner, self._budget, self._reserve = inner, budget, reserve

    def generate(self, request: LLMRequest) -> LLMResponse:
        if self._budget.remaining() <= self._reserve:
            raise BudgetExceeded(self._budget.limit)
        return self._inner.generate(request)


@dataclass(frozen=True)
class LoopSettings:
    supervisor_model: str | None = None
    worker_model: str | None = None
    max_parallel: int = 2
    task_max_chars: int = 1000
    revision_iterations: int = 2
    approval_timeout_min: int = 30
    supervisor_context_tokens: int = 6000
    worker_context_chars: int = 17_500
    writer_context_chars: int = 42_000  # WRITER_CONTEXT_TOKENS × 3.5 (the Writer reads evidence)
    memory_limit: int = 3
    budget_per_iteration: int = 3  # v1.1: budget = 3 × iterations left + 5
    budget_base: int = 5
    conversation_max_turns: int = 30  # v1.2: how many turns of a conversation are loaded


@dataclass(frozen=True)
class ChatContext:
    """v1.2: what one run knows about its conversation, built once per run from STORED runs
    (plan Improving_Chat_UI_UX §5–6). Never from text the client sends."""

    block: str = FIRST_TURN  # the fenced "Earlier turns in this chat" block
    shown: int = 0  # earlier turns in the block → run.context_turns
    scopes: tuple[str, ...] = ()  # run scopes of earlier turns with evidence, newest first
    run_ids: frozenset[str] = frozenset()  # every run of the conversation (memory exclusion)

    def tool_sources(self, sources: frozenset[SourceKind]) -> frozenset[SourceKind]:
        """The run's sources, plus the internal conversation family when it has scopes."""
        return sources | {SourceKind.CONVERSATION} if self.scopes else sources


class Supervisor:
    def __init__(
        self,
        runs: RunRepository,
        llm_for: Callable[[CallBudget], LLMClient],  # the per-run client over a budget
        registry: WorkerRegistry,
        prompts: PromptLibrary,
        secret: bytes,
        settings: LoopSettings,
        memory: MemoryStore | None = None,
        *,
        tools: ToolRegistry,
        retriever: Retriever,
    ) -> None:
        self.runs, self._llm_for, self.registry = runs, llm_for, registry
        self.prompts, self._secret, self.s, self.memory = prompts, secret, settings, memory
        self.tools, self.retriever = tools, retriever
        self.canary = canary_for(secret)
        self.output = OutputValidator(self.canary)

    # ---- entry point (runner thread) -------------------------------------------------------

    def run(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if run.status is not RunStatus.QUEUED:
            return  # cancelled or already handled
        lock = threading.Lock()
        left = max(run.iteration_limit - run.iteration, 1)
        budget = CallBudget(self.s.budget_per_iteration * left + self.s.budget_base)
        memo: ToolMemo = {}  # run-wide tool memo (v1.1)
        llm = self._llm_for(budget)
        fence = DataFence(self._secret, run.question)
        run.models = {
            "supervisor": self.s.supervisor_model or "",
            "workers": self.s.worker_model or "",
        }
        run.prompt_version = self.prompts.version
        run.start()
        self._save(run, lock)
        chat = ChatContext()
        try:
            chat = self._chat(run, fence)
            run.context_turns = chat.shown  # before the first decision (v1.2)
            if run.iteration == 0 and run.use_memory and self.memory and not run.memories:
                run.use_memories(
                    self.memory.recall(
                        run.question,
                        self.s.memory_limit,
                        run.question,
                        exclude_runs=chat.run_ids,
                    )
                )
            self._save(run, lock)
            self._loop(run, llm, budget, fence, lock, memo, chat)
        except (LLMError, BudgetExceeded) as err:
            self._degrade(run, _why(err))
        except CanaryLeak as err:
            run.add_flag("canary_leak")
            if not run.done:
                run.fail(str(err))
        except Exception:  # never kill the runner; the run records the failure
            logger.exception("run %s crashed", run.id)
            if not run.done:
                run.fail("Unexpected error while running the agents.")
        self._save(run, lock)
        if run.status is RunStatus.COMPLETED:
            self.remember(run)
        logger.info(
            "run=%s status=%s turn=%d context_turns=%d iterations=%d calls=%d cached=%d "
            "forced=%s flags=%s",
            run.id,
            run.status.value,
            run.turn,
            run.context_turns,
            run.iteration,
            run.llm_calls,
            run.cached_calls,
            run.forced,
            ",".join(run.flags),
        )

    def _chat(self, run: ResearchRun, fence: DataFence) -> ChatContext:
        """The earlier turns of the run's conversation, read from the repository. Scopes come
        only from runs whose conversation_id equals this run's (the repository query)."""
        if run.turn <= 1:
            return ChatContext(run_ids=frozenset({run.id}))
        conversation = [
            r
            for r in self.runs.conversation(run.conversation_id, self.s.conversation_max_turns)
            if r.conversation_id == run.conversation_id
        ]
        block, shown = conversation_context(earlier_turns(conversation, run), fence)
        earlier = sorted(
            (r for r in conversation if r.id != run.id and r.turn < run.turn and r.evidence),
            key=lambda r: r.turn,
            reverse=True,
        )
        scopes = tuple(f"run:{r.id}" for r in earlier[:MAX_SCOPES])
        return ChatContext(block, shown, scopes, frozenset({run.id, *(r.id for r in conversation)}))

    def remember(self, run: ResearchRun) -> None:
        if self.memory is not None and run.use_memory and run.final_output:
            self.memory.record(memory_from_run(run))

    # ---- the loop ----------------------------------------------------------------------

    def _loop(
        self,
        run: ResearchRun,
        llm: LLMClient,
        budget: CallBudget,
        fence: DataFence,
        lock: threading.Lock,
        memo: ToolMemo,
        chat: ChatContext,
    ) -> None:
        while run.status is RunStatus.RUNNING:
            if run.iteration >= run.iteration_limit:
                why = "the iteration limit was reached"
                return self._force(run, llm, budget, fence, lock, why, chat)
            if budget.remaining() <= RESERVE:
                why = "the LLM call budget was reached"
                return self._force(run, llm, budget, fence, lock, why, chat)
            run.agent_started("supervisor", None)
            self._save(run, lock)
            decision, meta = self._decide(run, llm, budget, fence, chat)
            if decision is None:
                run.notice("The Supervisor's reply broke the protocol twice.", "protocol", meta)
                why = "the Supervisor's replies were invalid"
                return self._force(run, llm, budget, fence, lock, why, chat)
            run.record_decision(decision, meta)
            self._save(run, lock)
            if decision.final is not None:
                self._finish(run, decision.final, forced=False, degraded=False)
                return None
            guarded = ReserveGuard(llm, budget)
            self._run_workers(run, decision.delegations, guarded, fence, lock, memo, chat)
        return None

    def _system(self) -> str:
        parallel = (
            f"You may send up to {self.s.max_parallel} DELEGATE/TASK pairs in one reply, to "
            "different workers, when their work is independent: reviewer and fact_checker on "
            "the same draft."
            if self.s.max_parallel > 1
            else ""
        )
        hint = " and fact_checker (in parallel)" if "fact_checker" in self.registry.names else ""
        return self.prompts.supervisor_system(self.registry.describe(), parallel, hint, self.canary)

    def _task_prompt(
        self,
        run: ResearchRun,
        fence: DataFence,
        budget: CallBudget,
        chat: ChatContext,
        forced: bool = False,
    ) -> str:
        left = 0 if forced else max(0, run.iteration_limit - run.iteration)
        return self.prompts.supervisor_task(
            earlier_turns=chat.block,
            question=fence.wrap(run.question, "question"),
            guidance=depth_block(run.depth),  # placeholder names: test_architecture
            sources=sources_block(chat.tool_sources(run.sources), run.workspace_id),
            memories=memory_block(run, fence),
            evidence=evidence_block(run),
            transcript=transcript_view(run, fence, self.s.supervisor_context_tokens),
            iterations_left=budget_line(left, budget),
            last_hint=last_hint(left),
        )

    def _request(self, messages: list) -> LLMRequest:
        return LLMRequest(
            system=self._system(),
            messages=messages,
            max_output_tokens=4096,
            thinking_budget=0,
            model=self.s.supervisor_model,
        )

    def _decide(
        self,
        run: ResearchRun,
        llm: LLMClient,
        budget: CallBudget,
        fence: DataFence,
        chat: ChatContext,
    ) -> tuple[Decision | None, CallMeta]:
        """One Supervisor call + parse; on a protocol error, ONE repair call."""
        counter = StepCounter(llm, self.output)
        user = UserMessage(self._task_prompt(run, fence, budget, chat))
        response = counter.generate(self._request([user]))
        reply = response.text or ""
        try:
            decision = self._parse(reply, cut=response.finish_reason == "max_tokens")
            self._check_order(decision, run)
            return decision, counter.meta(self.s.supervisor_model)
        except ProtocolError as err:
            if budget.remaining() <= RESERVE:  # keep the last call for the forced completion
                return None, counter.meta(self.s.supervisor_model)
            logger.info("run=%s protocol error, repairing", run.id)
            repair = UserMessage(self.prompts.supervisor_repair(str(err)))
            again = counter.generate(self._request([user, AssistantMessage(reply, []), repair]))
            try:
                cut = again.finish_reason == "max_tokens"
                decision = replace(self._parse(again.text or "", cut=cut), repaired=True)
                self._check_order(decision, run)
            except ProtocolError:
                return None, counter.meta(self.s.supervisor_model)
            return decision, counter.meta(self.s.supervisor_model)

    def _parse(
        self, reply: str, workers: frozenset[str] | None = None, cut: bool = False
    ) -> Decision:
        if cut:
            raise ProtocolError("Your reply was cut off because it was too long; keep it shorter.")
        return parse_decision(
            reply,
            self.registry.names if workers is None else workers,
            self.s.max_parallel,
            self.s.task_max_chars,
        )

    @staticmethod
    def _check_order(decision: Decision, run: ResearchRun) -> None:
        """The three order rules that only waste calls when broken (plan §3.6). `parse_decision`
        stays pure; a violation goes through the same single repair call."""
        workers = {d.worker for d in decision.delegations}
        if not workers:
            return
        if "writer" in workers and workers & REVIEWERS:
            raise ProtocolError(ORDER_TOGETHER)
        if workers & REVIEWERS and run.latest_result("writer") is None:
            raise ProtocolError(ORDER_NO_DRAFT)
        if (
            "writer" in workers
            and run.iteration == 0
            and not run.evidence
            and run.latest_result("researcher") is None
        ):
            raise ProtocolError(ORDER_NO_EVIDENCE)

    # ---- workers -------------------------------------------------------------------------

    def _run_workers(
        self,
        run: ResearchRun,
        delegations: tuple[Delegation, ...],
        llm: LLMClient,
        fence: DataFence,
        lock: threading.Lock,
        memo: ToolMemo,
        chat: ChatContext,
    ) -> None:
        results = {
            name: r for name in self.registry.names if (r := run.latest_result(name)) is not None
        }
        history: dict[str, tuple[WorkerResult, ...]] = {}
        for e in run.transcript:
            if e.result is not None and not e.result.failed:
                history[e.result.worker] = (*history.get(e.result.worker, ()), e.result)
        snapshot = list(run.evidence)

        def add_evidence(chunks: list[EvidenceChunk]) -> list[EvidenceRef]:
            with lock:
                return run.add_evidence(chunks)

        def evidence_by_n(n: int) -> EvidenceRef | None:
            with lock:
                return run.evidence_by_n(n)

        def evidence_count() -> int:
            with lock:
                return len(run.evidence)

        def record_tool_call(record: ToolCallRecord, meta: CallMeta) -> None:
            with lock:
                run.record_tool_call(record, meta)
                self._save_locked(run)

        read_scopes = (
            [*([f"ws:{run.workspace_id}"] if run.workspace_id else []), "demo"]
            if SourceKind.DOCUMENTS in run.sources
            else []
        )
        tool_ctx = ToolContext(
            run.id,
            f"run:{run.id}",
            read_scopes,
            add_evidence,
            evidence_by_n,
            fence,
            sources=run.sources,
            evidence_count=evidence_count,
            conversation_scopes=chat.scopes,
        )
        tool_sources = chat.tool_sources(run.sources)

        def one(d: Delegation) -> tuple[Delegation, WorkerOutput, CallMeta, bool]:
            previous = run.previous_result(d)
            if previous is not None:  # the same TASK again: reuse, 0 calls
                return d, WorkerOutput(previous), CallMeta(self.s.worker_model), True
            worker = self.registry.get(d.worker)
            with lock:
                run.agent_started(d.worker, d.task)
                self._save_locked(run)
            tools = (
                self.tools.box(tool_ctx, tool_sources, memo) if d.worker == "researcher" else None
            )
            ctx = WorkerContext(
                run_id=run.id,
                question=run.question,
                depth=run.depth,
                sources=run.sources,
                results={k: v for k, v in results.items() if k in worker.needs},
                evidence=list(snapshot),
                fence=fence,
                tools=tools,
                retriever=self.retriever,
                record_tool_call=record_tool_call,
                model=self.s.worker_model,
                canary=self.canary,
                context_chars=(
                    self.s.writer_context_chars
                    if d.worker == "writer"
                    else self.s.worker_context_chars
                ),
                history={k: v for k, v in history.items() if k in worker.needs},
                earlier_turns=chat.block,
            )
            counter = StepCounter(llm, self.output)
            try:
                out = worker.run(d.task, ctx, counter)
            except (LLMBadResponse, LLMRequestRejected) as err:
                logger.info("run=%s worker=%s failed: %s", run.id, d.worker, type(err).__name__)
                out = WorkerOutput(WorkerResult(d.worker, f"Worker failed: {err}", failed=True))
            except BudgetExceeded:
                logger.info("run=%s worker=%s stopped: budget", run.id, d.worker)
                out = WorkerOutput(
                    WorkerResult(d.worker, BUDGET_STOP, failed=True),
                    [(f"The {d.worker} stopped: the run's LLM budget is used up.", "budget")],
                )
            return d, out, counter.meta(self.s.worker_model), False

        if len(delegations) == 1:
            outcomes = [one(delegations[0])]
        else:  # extension: parallel workers (same context snapshot, results in order)
            with ThreadPoolExecutor(len(delegations), thread_name_prefix="worker") as pool:
                futures = [pool.submit(one, d) for d in delegations]
                outcomes = [f.result() for f in futures]
        for d, out, meta, repeated in outcomes:
            content, redacted = self.output.clean(out.result.content)
            with lock:
                if repeated:
                    run.notice(
                        f"Repeated delegation to {d.worker}: the earlier result was "
                        "reused (0 calls).",
                        "repeated",
                    )
                for text, flag in out.notices:
                    run.notice(text, flag)
                if redacted:
                    run.notice("Sensitive-looking data was redacted from a result.", "redacted")
                run.record_result(d.task, replace(out.result, content=content), meta)
                self._save_locked(run)

    # ---- completion ----------------------------------------------------------------------

    def _force(
        self,
        run: ResearchRun,
        llm: LLMClient,
        budget: CallBudget,
        fence: DataFence,
        lock: threading.Lock,
        why: str,
        chat: ChatContext,
    ) -> None:
        """One call that may ONLY answer FINAL; anything else → the fallback chain."""
        run.notice(f"Forced completion: {why}.", "forced")
        run.agent_started("supervisor", None)
        self._save(run, lock)
        counter = StepCounter(llm, self.output)
        user = (
            self._task_prompt(run, fence, budget, chat, forced=True)
            + "\n\n"
            + self.prompts.supervisor_forced()
        )
        response = counter.generate(self._request([UserMessage(user)]))
        try:
            cut = response.finish_reason == "max_tokens"
            decision = replace(self._parse(response.text or "", frozenset(), cut), forced=True)
        except ProtocolError:
            run.notice(
                "The forced reply was not a valid FINAL.",
                "protocol",
                counter.meta(self.s.supervisor_model),
            )
            return self._degrade(run, f"{why} and the forced answer was invalid")
        run.record_decision(decision, counter.meta(self.s.supervisor_model), count=False)
        self._finish(run, decision.final or "", forced=True, degraded=False)
        return None

    def _finish(self, run: ResearchRun, text: str, *, forced: bool, degraded: bool) -> None:
        """FINAL → report (plan §3.4): citations → redaction → title → parse_report →
        set_report → propose_final. No Sources section is appended: the report rebuilds its
        references from the cited evidence."""
        text, _, unknown = clean_citations(text, {r.n for r in run.evidence})
        if unknown:
            run.notice(
                f"Removed {len(unknown)} citation(s) to unknown evidence from the final report.",
                "unknown_citation",
            )
        text, redacted = self.output.clean(text)
        if redacted:
            run.notice("Sensitive-looking data was redacted from the report.", "redacted")
        text = text.strip()
        if not _TITLE.search(text):
            text = f"# {_one_line(run.question)}\n\n{text}"
        snippets = snippets_for(run.evidence, cited_numbers(text), self.retriever.get)
        run.set_report(
            parse_report(
                text, run.evidence, question=run.question, depth=run.depth, snippets=snippets
            )
        )
        run.propose_final(
            text, forced=forced, degraded=degraded, approval_timeout_min=self.s.approval_timeout_min
        )

    def _degrade(self, run: ResearchRun, why: str) -> None:
        """Fallback chain (plan §3.5): latest Writer draft → Researcher findings → fail. The
        note is a closing section, so the report's summary stays meaningful."""
        if run.status is not RunStatus.RUNNING:
            return
        draft, notes = run.latest_result("writer"), run.latest_result("researcher")
        note = (
            "## About this report\n\n"
            f"Assembled automatically because {why}. The Supervisor did not finalize it."
        )
        if draft is not None:
            body = f"{draft.content.strip()}\n\n{note}"
        elif notes is not None:
            findings = _HEADING.sub("### ", notes.content.strip())
            body = (
                f"# {_one_line(run.question)}\n\n{_first_paragraph(findings)}\n\n"
                f"## Research findings\n\n{findings}\n\n{note}"
            )
        else:
            run.fail(f"No result could be produced: {why}.")
            return
        run.notice(f"Report assembled automatically: {why}.", "degraded")
        self._finish(run, body, forced=True, degraded=True)

    # ---- persistence -----------------------------------------------------------------------

    def _save(self, run: ResearchRun, lock: threading.Lock) -> None:
        with lock:
            self._save_locked(run)

    def _save_locked(self, run: ResearchRun) -> None:
        self.runs.save(run)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _first_paragraph(markdown: str, limit: int = 600) -> str:
    """The first paragraph that is not a heading, as one line (the degraded summary)."""
    for block in markdown.split("\n\n"):
        lines = [x.strip().lstrip("-*+ ").strip() for x in block.splitlines()]
        lines = [x for x in lines if x and not x.startswith("#")]
        if lines:
            text = " ".join(lines)
            return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"
    return "The research findings are below."


def _why(err: Exception) -> str:
    if isinstance(err, BudgetExceeded):
        return "the LLM call budget was used up"
    name = type(err).__name__
    if name == "LLMQuotaExceeded":
        return "the LLM quota was reached"
    if name in ("LLMUnavailable", "LLMOverloaded", "LLMTimeout"):
        return "the LLM service was unavailable"
    return "the LLM service refused the request"
