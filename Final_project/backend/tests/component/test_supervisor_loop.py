"""L1–L12: the Supervisor loop over stub workers, a stub tool and a stub memory store
(SUPERVISOR_PLAN §9). The Supervisor is built directly, with the real per-run LLM stack
(RunCounter → Caching → Budgeted) over a scripted model: 0 real calls, 0 network."""

import threading
from dataclasses import dataclass, field
from typing import Any

from fakes import USAGE, FakeRetriever

from app.application.budget import BudgetedLLMClient, CallBudget, RunCounter
from app.application.prompts import PromptLibrary
from app.application.supervisor import (
    BUDGET_STOP,
    ORDER_NO_EVIDENCE,
    LoopSettings,
    ReserveGuard,
    Supervisor,
)
from app.application.tools.base import ToolContext, ToolOutput, ToolRegistry
from app.application.workers.base import WorkerContext, WorkerOutput, WorkerRegistry
from app.domain.depth import Depth
from app.domain.errors import BudgetExceeded, LLMUnavailable
from app.domain.evidence import EvidenceChunk, SourceKind
from app.domain.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    Memory,
    ToolSpec,
    UserMessage,
)
from app.domain.run import ResearchRun, RunStatus
from app.domain.transcript import CallMeta, ToolCallRecord, WorkerResult
from app.infrastructure.caching_llm import CachingLLMClient
from app.infrastructure.sqlite_store import SqliteRunRepository

QUESTION = "How do vector databases index embeddings?"
DRAFT = "# Vector indexes\n\nThey use ANN structures [E1].\n\n## HNSW\n\nGraph layers [E2]."


def say(text: str, finish: str = "stop") -> LLMResponse:
    return LLMResponse(text, [], None, USAGE, finish)  # type: ignore[arg-type]


def delegate(*pairs: tuple[str, str]) -> str:
    return "REASON: next.\n" + "\n".join(f"DELEGATE: {w}\nTASK: {t}" for w, t in pairs)


def final(text: str = DRAFT) -> str:
    return f"REASON: done.\nFINAL:\n{text}"


HAPPY = [
    delegate(("researcher", "Find how ANN indexes work.")),
    delegate(("writer", "Write the report.")),
    delegate(("reviewer", "Review the draft."), ("fact_checker", "Check the claims.")),
    final(),
]


class Model:
    """Supervisor replies from a queue (str, LLMResponse, exception, or prompt → str);
    workers get 'ok'."""

    def __init__(self, supervisor: list[Any] | None = None) -> None:
        self.supervisor = list(supervisor or [])
        self.requests: list[LLMRequest] = []
        self._lock = threading.Lock()

    def generate(self, request: LLMRequest) -> LLMResponse:
        with self._lock:
            self.requests.append(request)
            if "You are the Supervisor" not in request.system:
                return say("ok")
            item = self.supervisor.pop(0) if self.supervisor else final()
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(request.messages[-1].text)
        return item if isinstance(item, LLMResponse) else say(item)

    def prompts(self) -> list[str]:
        return [
            "\n".join(m.text for m in r.messages if isinstance(m, UserMessage))
            for r in self.requests
            if "You are the Supervisor" in r.system
        ]


# ---- stubs -------------------------------------------------------------------------------


def chunk(i: int) -> EvidenceChunk:
    return EvidenceChunk(
        f"chunk{i}", "run:x", SourceKind.WEB, f"https://ex.org/{i}", f"Page {i}",
        f"https://ex.org/{i}", None, None, None, None, 0, f"Text of page {i}.", f"h{i}", "",
    )  # fmt: skip


class StubSearch:
    """A search_web tool that numbers two chunks per call."""

    name, family = "search_web", SourceKind.WEB
    spec = ToolSpec("search_web", "Search the web.", {"type": "object"})

    def __init__(self) -> None:
        self.calls = 0
        self.contexts: list[ToolContext] = []

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolOutput:
        self.calls += 1
        self.contexts.append(ctx)
        base = 2 * (self.calls - 1)
        refs = ctx.add_evidence([chunk(base + 1), chunk(base + 2)])
        record = ToolCallRecord(
            "researcher", "search_web", {"query": args["query"]}, "ok", "searxng", None, 2,
            tuple(r.n for r in refs), duration_ms=123 * self.calls,
        )  # fmt: skip
        return ToolOutput("RESULT search_web", record)


@dataclass
class Stub:
    name: str
    content: str = "ok"
    calls: int = 1  # LLM calls per task; 0 = none, -1 = until the budget stops it
    use_tool: bool = False
    needs: frozenset[str] = frozenset()
    error: Exception | None = None
    contexts: list[WorkerContext] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.name.title()

    @property
    def description(self) -> str:
        return f"the {self.name}"

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        self.contexts.append(ctx)
        if self.error is not None:
            raise self.error
        ns: tuple[int, ...] = ()
        if self.use_tool and ctx.tools is not None:
            out = ctx.tools.call("search_web", {"query": task})
            ctx.record_tool_call(out.record, CallMeta())
            ns = out.record.evidence_ns
        n = 0
        while self.calls < 0 or n < self.calls:
            llm.generate(LLMRequest(f"You are the {self.name}", [UserMessage(task)]))
            n += 1
        return WorkerOutput(WorkerResult(self.name, self.content, {}, ns))


def workers(**overrides: Stub) -> list[Stub]:
    base = {
        "researcher": Stub("researcher", "Findings: ANN indexes trade recall for speed [E1].",
                           use_tool=True),
        "writer": Stub("writer", DRAFT, needs=frozenset({"researcher"})),
        "reviewer": Stub("reviewer", "Verdict: APPROVE"),
        "fact_checker": Stub("fact_checker", "Fact check: 2 of 2 supported."),
    }  # fmt: skip
    return list((base | overrides).values())


class StubMemory:
    def __init__(self, memories: list[Memory] | None = None) -> None:
        self.memories = memories or []
        self.recalls: list[tuple[str, int, str]] = []
        self.recorded: list[Memory] = []

    def recall(self, query: str, limit: int, exclude_question: str) -> list[Memory]:
        self.recalls.append((query, limit, exclude_question))
        return self.memories[:limit]

    def record(self, memory: Memory) -> None:
        self.recorded.append(memory)


class DictCache:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, key: str, value: dict) -> None:
        self.data[key] = value


@dataclass
class Rig:
    supervisor: Supervisor
    runs: SqliteRunRepository
    model: Model
    tool: StubSearch
    budgets: list[CallBudget]
    retriever: FakeRetriever

    def start(self, question: str = QUESTION, **kw: Any) -> ResearchRun:
        iterations = kw.pop("max_iterations", 5)
        run = ResearchRun.create(question, iterations, kw.pop("require_approval", False),
                                 kw.pop("use_memory", False), **kw)  # fmt: skip
        self.runs.create(run)
        self.supervisor.run(run.id)
        return self.runs.get(run.id)


def rig(
    tmp_path,
    model: Model | None = None,
    stubs: list[Stub] | None = None,
    memory: StubMemory | None = None,
    cache: DictCache | None = None,
) -> Rig:
    runs = SqliteRunRepository(str(tmp_path / "runs.db"))
    model = model or Model(list(HAPPY))
    budgets: list[CallBudget] = []
    tool = StubSearch()
    retriever = FakeRetriever([chunk(i) for i in range(1, 9)])

    def llm_for(budget: CallBudget) -> LLMClient:
        budgets.append(budget)
        inner: LLMClient = BudgetedLLMClient(model, budget)
        if cache is not None:
            inner = CachingLLMClient(inner, cache, "test-model")
        return RunCounter(inner, budget)

    supervisor = Supervisor(
        runs, llm_for, WorkerRegistry(stubs or workers()), PromptLibrary(), b"secret",
        LoopSettings(supervisor_model="sup", worker_model="wrk"), memory,
        tools=ToolRegistry([tool]), retriever=retriever,
    )  # fmt: skip
    return Rig(supervisor, runs, model, tool, budgets, retriever)


def actions(run: ResearchRun) -> list[str]:
    out = []
    for e in run.transcript:
        if e.decision is not None:
            out.append(e.decision.action + ":" + ",".join(d.worker for d in e.decision.delegations))
        elif e.tool is not None:
            out.append("tool")
        elif e.result is not None:
            out.append(e.result.worker + (" failed" if e.result.failed else ""))
        else:
            out.append(f"{e.kind.value}:{e.flag}")
    return out


# ---- the loop ------------------------------------------------------------------------------


# L1
def test_happy_path_produces_a_report_with_the_plan_inputs(tmp_path):
    r = rig(tmp_path)
    run = r.start(depth=Depth.BRIEF, sources=frozenset({SourceKind.WEB, SourceKind.DOCUMENTS}))
    assert run.status is RunStatus.COMPLETED and not run.forced and not run.degraded
    assert actions(run) == [
        "delegate:researcher", "tool", "researcher", "delegate:writer", "writer",
        "delegate:reviewer,fact_checker", "reviewer", "fact_checker", "final:",
    ]  # fmt: skip
    assert run.report.title == "Vector indexes" and run.report.depth is Depth.BRIEF
    assert [x.id for x in run.report.references] == ["E1", "E2"]
    assert run.report.references[0].snippet == "Text of page 1."  # snippets via the retriever
    assert run.final_output == DRAFT and "Sources" not in run.final_output
    # 4 Supervisor + 4 worker calls; budget = 3 × 5 + 5
    assert run.llm_calls == 8 and r.budgets[0].limit == 20
    first, *_, last = r.model.prompts()
    assert "# Depth\nDepth: brief — 200–400 words, 2–3 sections." in first
    assert "- documents: the demo library\nNot allowed for this run: wikipedia." in first
    assert "# Evidence so far\n(none yet)" in first
    assert "Decisions left: 5. LLM calls left: about 20 (" in first
    assert "Decisions left: 2. LLM calls left: about 13 (" in last
    assert "2 evidence chunks (web 2). Cited by the findings: 2." in last
    assert 'Tools: search_web ×1\nQueries: "Find how ANN indexes work."' in last
    assert "New evidence: E1–E2 (2 chunks)" in last and "123" not in last
    system = r.model.requests[0].system
    assert "and fact_checker (in parallel)" in system and "reviewer and fact_checker on" in system
    assert "Topics:" not in system and "<data" in system  # base.md data rule


def test_worker_context_tools_and_evidence(tmp_path):
    stubs = workers()
    r = rig(tmp_path, stubs=stubs)
    run = r.start(workspace_id="ws-1")
    researcher, writer = stubs[0], stubs[1]
    ctx = researcher.contexts[0]
    assert ctx.tools is not None and [s.name for s in ctx.tools.offered()] == ["search_web"]
    assert ctx.depth is Depth.DETAILED and ctx.evidence == [] and ctx.model == "wrk"
    tool_ctx = r.tool.contexts[0]
    assert tool_ctx.run_scope == f"run:{run.id}" and tool_ctx.read_scopes == ["ws:ws-1", "demo"]
    assert tool_ctx.sources == run.sources and tool_ctx.evidence_by_n(2).title == "Page 2"
    assert tool_ctx.evidence_by_n(3) is None and tool_ctx.evidence_count() == 2
    w = writer.contexts[0]
    assert w.tools is None and [e.n for e in w.evidence] == [1, 2]
    assert w.context_chars == 42_000 and ctx.context_chars == 17_500  # the Writer reads more
    assert set(w.results) == {"researcher"} and len(w.history["researcher"]) == 1
    assert run.tool_calls == 1 and run.transcript[1].iteration == 1  # stamped with the iteration


# L2 order rules through the repair path
def test_order_rule_is_repaired_with_the_rule_message(tmp_path):
    script = [delegate(("writer", "Write it.")), *HAPPY]
    r = rig(tmp_path, Model(script))
    run = r.start()
    assert run.status is RunStatus.COMPLETED and run.transcript[0].decision.repaired
    repair = r.model.requests[1].messages[-1].text
    assert ORDER_NO_EVIDENCE in repair and "did not follow the protocol" in repair
    assert actions(run)[0] == "delegate:researcher"


def test_order_rule_broken_twice_forces_completion(tmp_path):
    together = delegate(("writer", "Write."), ("reviewer", "Review."))
    r = rig(tmp_path, Model([together, together, final()]))
    run = r.start()
    assert actions(run)[:3] == ["notice:protocol", "notice:forced", "final:"]
    assert run.forced and run.status is RunStatus.COMPLETED and run.iteration == 0


# L3
def test_revision_round_and_cut_off_reply(tmp_path):
    cut = say("REASON: x\nFINAL:\n# Very long", finish="max_tokens")
    script = [
        *HAPPY[:3],
        delegate(("writer", "Revise: fix the unsupported HNSW claim.")),
        cut,
        final(),
    ]
    r = rig(tmp_path, Model(script))
    run = r.start()
    assert run.status is RunStatus.COMPLETED
    assert actions(run)[-3:] == ["delegate:writer", "writer", "final:"]
    assert run.transcript[-1].decision.repaired
    assert "cut off" in r.model.requests[-1].messages[-1].text


def test_repeated_task_is_reused_without_calls(tmp_path):
    again = delegate(("researcher", "Find how ANN indexes work."))
    r = rig(tmp_path, Model([again, again, final()]))
    run = r.start()
    assert actions(run)[:6] == [
        "delegate:researcher", "tool", "researcher", "delegate:researcher", "notice:repeated",
        "researcher",
    ]  # fmt: skip
    assert r.tool.calls == 1 and run.transcript[5].meta.real_calls == 0


def test_human_feedback_goes_to_the_prompt_and_revises(tmp_path):
    r = rig(tmp_path, Model([*HAPPY, delegate(("writer", "Add a table.")), final()]))
    run = r.start(require_approval=True)
    assert run.status is RunStatus.AWAITING_APPROVAL and run.draft == DRAFT
    assert run.report is not None and run.final_output is None
    run.reject("Please add a comparison table.", 2)
    r.runs.save(run)
    r.supervisor.run(run.id)
    run = r.runs.get(run.id)
    assert run.status is RunStatus.AWAITING_APPROVAL and actions(run)[-2:] == ["writer", "final:"]
    assert r.budgets[-1].limit == 3 * 2 + 5  # covers the 2 revision iterations
    prompt = r.model.prompts()[-2]
    assert 'kind="human_feedback">\nPlease add a comparison table.' in prompt
    assert "Decisions left: 2." in prompt


# ---- completion -------------------------------------------------------------------------------


# L4
def test_forced_at_the_budget_reserve(tmp_path):
    greedy = Stub("researcher", "notes", calls=6)
    count = iter(range(100))

    def endless(prompt: str) -> str:
        if "You have no decisions left" in prompt:
            return final("# Forced report\n\nSummary.")
        return delegate(("researcher", f"Find fact {next(count)}."))

    r = rig(tmp_path, Model([endless] * 20), workers(researcher=greedy))
    run = r.start(max_iterations=3)  # budget 14
    assert run.forced and run.status is RunStatus.COMPLETED
    assert run.llm_calls == r.budgets[0].real <= 14
    assert "notice:forced" in actions(run) and run.final_output.startswith("# Forced report")
    forced = next(e.text for e in run.transcript if e.flag == "forced")
    assert forced == "Forced completion: the LLM call budget was reached." and run.iteration == 2


def test_worker_hitting_the_budget_leaves_the_reserved_call_for_final(tmp_path):
    hog = Stub("researcher", "notes", calls=-1)
    r = rig(tmp_path, Model([HAPPY[0], final("# Report\n\nFrom the reserve.")]), workers(
        researcher=hog))  # fmt: skip
    run = r.start(max_iterations=1)  # budget 3 × 1 + 5 = 8
    assert run.status is RunStatus.COMPLETED and run.forced and not run.degraded
    failed = next(e.result for e in run.transcript if e.result is not None)
    assert (
        failed.failed
        and failed.content == BUDGET_STOP
        and "budget" in [e.flag for e in run.transcript]
    )
    assert r.budgets[0].real == 8 == run.llm_calls  # 1 + 6 worker calls + the reserved one
    assert run.final_output.startswith("# Report")


def test_reserve_guard_refuses_only_at_the_reserve():
    budget = CallBudget(3)
    guard = ReserveGuard(BudgetedLLMClient(Model(), budget), budget)
    request = LLMRequest("You are the writer", [UserMessage("x")])
    guard.generate(request)
    guard.generate(request)
    try:
        guard.generate(request)
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded:
        assert budget.remaining() == 1


# L5 degradation
def test_degrade_with_the_draft_with_findings_and_with_nothing(tmp_path):
    down = LLMUnavailable("down")
    r = rig(tmp_path / "a", Model([*HAPPY[:2], down]))
    run = r.start()
    assert run.degraded and run.forced and run.status is RunStatus.COMPLETED
    assert run.final_output.startswith(DRAFT + "\n\n## About this report\n\nAssembled")
    assert "because the LLM service was unavailable" in run.final_output
    assert run.report.summary == "They use ANN structures [E1]."

    r = rig(
        tmp_path / "b",
        Model([HAPPY[0], down]),
        workers(
            researcher=Stub("researcher", "## Notes\n\n- ANN is fast [E1].\n\n# Odd", use_tool=True)
        ),
    )
    run = r.start()  # fmt: skip
    assert run.final_output.startswith(f"# {QUESTION}\n\nANN is fast [E1].\n\n## Research findings")
    assert "### Notes" in run.final_output and "### Odd" in run.final_output
    assert run.report.title == QUESTION and run.report.summary.startswith("ANN is fast")
    assert [s.heading for s in run.report.sections] == ["Research findings", "About this report"]

    r = rig(tmp_path / "c", Model([down]))
    run = r.start()
    assert run.status is RunStatus.FAILED and "No result could be produced" in run.error


def test_forced_invalid_reply_degrades(tmp_path):
    r = rig(tmp_path, Model([*HAPPY[:2], "nonsense", "nonsense", "DELEGATE: writer\nTASK: x"]))
    run = r.start()
    assert run.degraded and "## About this report" in run.final_output
    assert "the forced answer was invalid" in run.final_output


def test_worker_errors(tmp_path):
    from app.domain.errors import LLMRequestRejected

    stubs = workers(writer=Stub("writer", error=LLMRequestRejected(400, "blocked")))
    r = rig(tmp_path / "a", Model([*HAPPY[:2], final()]), stubs)
    run = r.start()
    assert "writer failed" in actions(run) and run.status is RunStatus.COMPLETED

    stubs = workers(writer=Stub("writer", error=LLMUnavailable("down")))
    run = rig(tmp_path / "b", Model(HAPPY[:2]), stubs).start()
    assert run.degraded and run.final_output.startswith(f"# {QUESTION}")  # findings only


# L6 finish rules
def test_finish_cleans_citations_adds_a_title_and_drops_sources(tmp_path):
    answer = "Summary [E1] and [E 2] and [E7].\n\n## Body\n\nText [E1, E2].\n\n## Sources\n- x"
    r = rig(tmp_path, Model([HAPPY[0], final(answer)]))
    run = r.start()
    assert run.final_output.startswith(f"# {QUESTION}\n\nSummary [E1] and [E2] and .")
    assert "[E7]" not in run.final_output and "unknown_citation" in run.flags
    assert [s.heading for s in run.report.sections] == ["Body"]
    assert [x.id for x in run.report.references] == ["E1", "E2"]


# ---- memory, approval and the cache ------------------------------------------------------


def memory(i: int, question: str) -> Memory:
    return Memory(f"run_m{i}", question, f"Summary {i}.", [f"Ref {i}"], "2026-10-02T00:00:00")


# L7
def test_memory_recalled_once_fenced_and_recorded_on_completion(tmp_path):
    store = StubMemory([memory(1, "What is HNSW?"), memory(2, "What is IVF?")])
    r = rig(tmp_path, memory=store)
    run = r.start(use_memory=True)
    assert store.recalls == [(QUESTION, 3, QUESTION)]
    assert [m["run_id"] for m in run.memories] == ["run_m1", "run_m2"]
    prompts = r.model.prompts()
    assert all('kind="memory" run="run_m1"' in p for p in prompts)
    assert "Past report" in prompts[0] and "Summary: Summary 1." in prompts[0]
    assert len(store.recorded) == 1 and store.recorded[0].run_id == run.id
    assert store.recorded[0].question == QUESTION and store.recorded[0].reference_titles == [
        "Page 1", "Page 2"]  # fmt: skip


def test_memory_off_and_not_recorded_before_approval(tmp_path):
    store = StubMemory([memory(1, "What is HNSW?")])
    r = rig(tmp_path / "a", memory=store)
    run = r.start(use_memory=False)
    assert store.recalls == [] and store.recorded == [] and run.memories == []
    assert "(none)" in r.model.prompts()[0]

    r = rig(tmp_path / "b", memory=store)
    run = r.start(use_memory=True, require_approval=True)
    assert run.status is RunStatus.AWAITING_APPROVAL and store.recorded == []
    run.approve()
    r.supervisor.remember(run)
    assert [m.run_id for m in store.recorded] == [run.id]


# L8
def test_identical_rerun_sends_identical_supervisor_prompts_from_the_cache(tmp_path):
    cache = DictCache()
    store = StubMemory([memory(1, "What is HNSW?")])
    r = rig(tmp_path, Model(list(HAPPY)), memory=store, cache=cache)
    first = r.start(use_memory=True)
    real = len(r.model.requests)
    r.model.supervisor = list(HAPPY)
    second = r.start(use_memory=True)
    assert first.status is second.status is RunStatus.COMPLETED
    assert len(r.model.requests) == real  # 0 real calls on the rerun
    assert second.llm_calls == 0 and second.cached_calls == first.llm_calls == 8
    assert second.final_output == first.final_output


# ---- failure and safety --------------------------------------------------------------------


def test_canary_leak_and_crash_fail_the_run(tmp_path):
    r = rig(tmp_path / "a")
    leak = f"REASON: x\nFINAL:\n# T\n\nmarker {r.supervisor.canary}"
    r.model.supervisor = [leak]
    run = r.start()
    assert run.status is RunStatus.FAILED and "canary_leak" in run.flags

    r = rig(tmp_path / "b", Model([ZeroDivisionError("bug")]))
    assert r.start().status is RunStatus.FAILED


def test_not_queued_runs_are_left_alone(tmp_path):
    r = rig(tmp_path)
    run = r.start()
    r.supervisor.run(run.id)  # already completed: nothing happens
    assert len(r.model.requests) == 8


def test_budget_exceeded_on_a_supervisor_call_degrades(tmp_path):
    class Exhausting(Model):
        def generate(self, request: LLMRequest) -> LLMResponse:
            if len(self.requests) >= 3:
                raise BudgetExceeded(3)
            return super().generate(request)

    run = rig(tmp_path, Exhausting(list(HAPPY))).start()
    assert run.degraded and "budget was used up" in run.final_output
