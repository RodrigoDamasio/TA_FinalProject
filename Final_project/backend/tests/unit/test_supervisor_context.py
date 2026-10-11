"""C1–C9: what the Supervisor sees (SUPERVISOR_PLAN §3.2, §3.6, §3.7), pure functions only."""

import pytest

from app.application.budget import CallBudget
from app.application.context import (
    MIN_RESULT_CHARS,
    budget_line,
    depth_block,
    evidence_block,
    last_hint,
    memory_block,
    ranges,
    sources_block,
    tool_summary,
    transcript_view,
)
from app.application.prompts import estimate_tokens
from app.application.security import DataFence
from app.application.supervisor import (
    ORDER_NO_DRAFT,
    ORDER_NO_EVIDENCE,
    ORDER_TOGETHER,
    Supervisor,
)
from app.domain.depth import Depth
from app.domain.errors import ProtocolError
from app.domain.evidence import EvidenceChunk, SourceKind
from app.domain.run import ResearchRun
from app.domain.transcript import CallMeta, Decision, Delegation, ToolCallRecord, WorkerResult

FENCE = DataFence(b"secret", "q")
ALL = frozenset({SourceKind.WEB, SourceKind.WIKIPEDIA, SourceKind.DOCUMENTS})


def rec(tool="search_web", status="ok", ns=(), query="q", **kw) -> ToolCallRecord:
    args = {"query": query} if query is not None else {"evidence_id": "E1"}
    return ToolCallRecord("researcher", tool, args, status, evidence_ns=tuple(ns), **kw)


def chunk(i: int, kind: SourceKind = SourceKind.WEB) -> EvidenceChunk:
    return EvidenceChunk(
        f"c{i}", "run:x", kind, f"k{i}", f"T{i}", None, None, None, None, None, i, "t", "h", ""
    )


def new_run(**kw) -> ResearchRun:
    run = ResearchRun.create("What is RAG and when is it useful?", 5, False, False, **kw)
    run.start()
    return run


def delegate(run: ResearchRun, *workers: str) -> None:
    run.record_decision(
        Decision(tuple(Delegation(w, f"task for {w}") for w in workers), None, "r"), CallMeta()
    )


def result(run: ResearchRun, worker: str, content: str, ns=(), failed=False) -> None:
    run.record_result(
        f"task for {worker}",
        WorkerResult(worker, content, {}, tuple(ns), failed=failed),
        CallMeta(),
    )


# C1
@pytest.mark.parametrize(
    ("depth", "head"),
    [
        (Depth.BRIEF, "Depth: brief — 200–400 words, 2–3 sections."),
        (Depth.DETAILED, "Depth: detailed — 600–1000 words, 4–6 sections."),
        (Depth.TECHNICAL, "Depth: technical — 800–1400 words, 5–7 sections."),
    ],
)
def test_depth_block(depth, head):
    first, guidance = depth_block(depth).split("\n")
    assert first == head and len(guidance) > 50


# C2
def test_sources_block_with_and_without_workspace_and_disallowed_families():
    full = sources_block(ALL, "ws-1")
    assert full.splitlines() == [
        "- web: the web (search engines) and full pages of web results",
        "- wikipedia: Wikipedia",
        "- documents: the user's uploaded documents and the demo library",
    ]
    demo = sources_block(frozenset({SourceKind.DOCUMENTS}), None)
    assert demo.splitlines() == [
        "- documents: the demo library",
        "Not allowed for this run: web, wikipedia.",
    ]
    assert "no source family" in sources_block(frozenset(), None)


# C3
def test_evidence_block_counts_by_kind_and_cited_by_findings():
    run = new_run()
    assert evidence_block(run) == "(none yet)"
    run.add_evidence(
        [chunk(1), chunk(2), chunk(3, SourceKind.WIKIPEDIA), chunk(4, SourceKind.DOCUMENTS)]
    )
    delegate(run, "researcher")
    result(run, "researcher", "x [E1][E3]", ns=(1, 3, 99))  # 99 was never issued
    result(run, "researcher", "failed", ns=(4,), failed=True)
    result(run, "writer", "draft [E2]", ns=(2,))  # the writer's citations don't count
    assert evidence_block(run) == (
        "4 evidence chunks (web 2, wikipedia 1, documents 1). Cited by the findings: 2."
    )


# C4
def test_budget_line_counts_real_and_cached_calls():
    budget = CallBudget(20)
    budget.take()
    budget.take()
    budget.count_cached()
    line = budget_line(3, budget)
    assert line.startswith("Decisions left: 3. LLM calls left: about 17 (")
    cached = CallBudget(20)  # a cached rerun: same counts, same line
    cached.take()
    for _ in range(2):
        cached.count_cached()
    assert budget_line(3, cached) == line
    assert last_hint(1).endswith("reply with FINAL.") and last_hint(2) == ""
    exhausted = CallBudget(1)
    exhausted.take()
    exhausted.count_cached()
    assert "about 0 " in budget_line(0, exhausted)


# C5
def test_tool_summary_counts_statuses_fallback_cap_queries_and_ranges():
    records = [
        rec(ns=(4, 5, 6), query="bge-small ONNX memory"),
        rec(status="empty", ns=(), query="fastembed   memory footprint 2026"),
        rec(tool="read_result", ns=(7,), query=None),
        rec(status="error", fallback="wikipedia", ns=(9, 10), query="x" * 120),
        rec(tool="search_documents", status="refused", ns=()),
        rec(status="capped", ns=(), query="bge-small ONNX memory"),
    ]
    text = tool_summary(records, earlier={5})
    lines = text.splitlines()
    assert lines[0] == (
        "Tools: search_web ×4 (1 empty, 1 error), read_result ×1, search_documents ×1 "
        "(1 refused) · fallback: wikipedia ×1 · cap reached"
    )
    queries = lines[1].removeprefix("Queries: ").split(" · ")
    assert queries[:2] == ['"bge-small ONNX memory"', '"fastembed memory footprint 2026"']
    assert len(queries) == 3 and len(queries[2]) == 80 + 2 and queries[2].endswith('…"')
    assert lines[2] == "New evidence: E4, E6–E7, E9–E10 (5 chunks)"
    assert "ms" not in text and "duration" not in text  # no durations: cache-stable
    assert tool_summary([]) == "Tools: none used"
    assert tool_summary([rec(ns=(1,))], earlier={1}).endswith("New evidence: none")
    assert ranges([3, 1, 2, 2, 8]) == "E1–E3, E8"


def test_tool_summary_never_shows_durations():
    slow, fast = rec(ns=(1,), duration_ms=9000), rec(ns=(1,), duration_ms=10)
    assert tool_summary([slow]) == tool_summary([fast])


# C6
def test_tool_records_fold_under_the_right_researcher_result():
    run = new_run()
    delegate(run, "researcher")
    run.add_evidence([chunk(1), chunk(2)])
    run.record_tool_call(rec(ns=(1, 2), query="first query"), CallMeta())
    result(run, "researcher", "Findings one [E1].", ns=(1,))
    run.notice("search_web fell back to wikipedia", "fallback")  # tool noise: hidden
    run.notice("Used 1 memories.", "memory")
    delegate(run, "researcher")
    run.add_evidence([chunk(3)])
    run.record_tool_call(rec(ns=(2, 3), query="second query"), CallMeta())
    result(run, "researcher", "Findings two [E3].", ns=(3,))
    view = transcript_view(run, FENCE, 6000)
    first, second = view.split("Result from researcher:")[1:]
    assert '"first query"' in first and '"second query"' not in first
    assert "New evidence: E1–E2 (2 chunks)" in first
    assert '"second query"' in second and "New evidence: E3 (1 chunks)" in second
    # the summary is inside the fenced block (queries are model-written text)
    assert second.index('kind="worker_result"') < second.index("Tools: search_web ×1")
    assert "[note] Used 1 memories." in view and "fell back" not in view
    assert "TOOL_CALL" not in view


def test_transcript_shows_latest_draft_review_and_fact_check_in_full_and_trims_oldest_first():
    run = new_run()
    delegate(run, "researcher")
    result(run, "researcher", "R" * 3000)
    delegate(run, "writer")
    result(run, "writer", "W" * 2000)
    delegate(run, "writer")
    result(run, "writer", "V" * 2500)
    delegate(run, "reviewer", "fact_checker")
    result(run, "reviewer", "Verdict: REVISE " + "x" * 1500)
    result(run, "fact_checker", "Fact check: " + "y" * 1500)
    view = transcript_view(run, FENCE, 100_000)
    assert "V" * 2500 in view and "x" * 1500 in view and "y" * 1500 in view
    assert "W" * 1200 in view and "W" * 1201 not in view  # older draft trimmed
    assert "R" * 1200 in view and "R" * 1201 not in view
    tight = transcript_view(run, FENCE, estimate_tokens(view) - 100)  # trims one result
    assert "R" * MIN_RESULT_CHARS in tight and "R" * (MIN_RESULT_CHARS + 1) not in tight
    assert "W" * 1200 in tight and "V" * 2500 in tight  # oldest first; latest never trimmed
    assert transcript_view(new_run(), FENCE, 6000).startswith("(nothing yet")


def test_failed_results_and_final_decisions_render():
    run = new_run()
    delegate(run, "writer")
    result(run, "writer", "Worker failed: x", failed=True)
    run.record_decision(Decision((), "# Report", "done"), CallMeta())
    view = transcript_view(run, FENCE, 6000)
    assert "Result from writer (failed):" in view and "You proposed a FINAL report" in view


# C7
def test_memory_block_is_fenced_with_date_summary_and_references():
    run = new_run()
    assert memory_block(run, FENCE) == "(none)"
    run.memories = [
        {
            "run_id": "run_1",
            "question": "What is BM25?",
            "summary": "word " * 200,
            "created_at": "2026-10-02T10:00:00+00:00",
            "reference_titles": [f"T{i}" for i in range(8)],
        },
        {"run_id": "run_2", "question": "Old shape?", "summary": "Short."},
    ]
    block = memory_block(run, FENCE)
    first, second = block.split("\n\n")
    assert 'kind="memory" run="run_1"' in first
    assert "Past report (2026-10-02): What is BM25?" in first
    summary = next(x for x in first.splitlines() if x.startswith("Summary: "))
    assert len(summary) <= len("Summary: ") + 601 and summary.endswith("…")
    assert "References: T0; T1; T2; T3; T4\n" in first
    assert "Past report: Old shape?\nSummary: Short.\n</data" in second


# C8 order rules (plan §3.6)
def decision(*workers: str) -> Decision:
    return Decision(tuple(Delegation(w, "t") for w in workers), None, None)


def test_order_rules_raise_the_exact_messages():
    run = new_run()
    check = Supervisor._check_order
    with pytest.raises(ProtocolError) as err:
        check(decision("writer", "reviewer"), run)
    assert str(err.value) == ORDER_TOGETHER
    for workers in (("reviewer",), ("fact_checker",), ("reviewer", "fact_checker")):
        with pytest.raises(ProtocolError) as err:
            check(decision(*workers), run)
        assert str(err.value) == ORDER_NO_DRAFT
    with pytest.raises(ProtocolError) as err:
        check(decision("writer"), run)
    assert str(err.value) == ORDER_NO_EVIDENCE


def test_valid_orders_pass():
    run = new_run()
    check = Supervisor._check_order
    check(decision("researcher"), run)
    check(Decision((), "# Final", None), run)  # FINAL is never order-checked
    delegate(run, "researcher")
    result(run, "researcher", "notes")
    check(decision("writer"), run)
    result(run, "writer", "Worker failed", failed=True)
    with pytest.raises(ProtocolError):
        check(decision("reviewer"), run)  # a failed draft is no draft
    result(run, "writer", "# Draft")
    check(decision("reviewer", "fact_checker"), run)
    # evidence without findings (e.g. after a human rejection) also allows the writer
    later = new_run()
    later.add_evidence([chunk(1)])
    check(decision("writer"), later)


# C9 degradation helpers
def test_first_paragraph_and_reasons():
    from app.application.supervisor import _first_paragraph, _why
    from app.domain.errors import BudgetExceeded, LLMQuotaExceeded, LLMRequestRejected

    assert _first_paragraph("### Only a heading") == "The research findings are below."
    long = _first_paragraph("### H\n\n- " + "word " * 300)
    assert len(long) <= 601 and long.endswith("…") and long.startswith("word")
    assert _why(BudgetExceeded(3)) == "the LLM call budget was used up"
    assert _why(LLMQuotaExceeded("day", 1)) == "the LLM quota was reached"
    assert _why(LLMRequestRejected(400, "x")) == "the LLM service refused the request"
