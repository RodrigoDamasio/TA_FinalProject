"""What the Supervisor sees (SUPERVISOR_PLAN §3.2): the depth, the allowed sources, evidence
COUNTS (never evidence text or titles), the recalled memories, its view of the transcript and
the limits. Worker results, memories and human feedback are untrusted text, so each one is
fenced. Every function here is pure and deterministic: no timestamps and no durations reach
the prompt, so an identical rerun hits the response cache. Owner: SUPERVISOR.

Transcript budget rule (M5): results are first trimmed to RESULT_CHARS each; the latest Writer
draft and the latest Reviewer and Fact-Checker results are always shown in full (FINAL is
built from them); if the prompt is still over budget, older results are trimmed further,
oldest first. TOOL_CALL entries are not shown one by one: they are folded into a tool-use
summary inside the fenced block of the result they belong to."""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from app.domain.depth import PROFILES, Depth
from app.domain.evidence import REQUESTABLE_SOURCES, SourceKind
from app.domain.run import ResearchRun, RunStatus
from app.domain.transcript import EntryKind, ToolCallRecord, TranscriptEntry

from .budget import CallBudget
from .prompts import estimate_tokens
from .security import DataFence
from .workers.base import trim

RESULT_CHARS = 1200
MIN_RESULT_CHARS = 300
MEMORY_SUMMARY_CHARS = 600
MEMORY_REFERENCES = 5
QUERY_CHARS = 80
MAX_QUERIES = 3
SHOWN_IN_FULL = ("writer", "reviewer", "fact_checker")  # their latest result, never trimmed
# Notices that only repeat what the tool-use summary already says.
TOOL_NOISE = frozenset({"tool", "tool_call", "capped", "repeat", "fallback"})

SOURCE_ORDER = (
    SourceKind.WEB,
    SourceKind.WIKIPEDIA,
    SourceKind.DOCUMENTS,
    SourceKind.CONVERSATION,  # v1.2: added by the app on follow-ups, never requested
)
SOURCE_TEXT = {
    SourceKind.WEB: "the web (search engines) and full pages of web results",
    SourceKind.WIKIPEDIA: "Wikipedia",
    SourceKind.DOCUMENTS: "the user's uploaded documents and the demo library",
    SourceKind.CONVERSATION: "the sources gathered in earlier turns of this chat",
}
STATUS_ORDER = ("empty", "error", "refused")


# ---- fixed blocks ------------------------------------------------------------------------


def depth_block(depth: Depth) -> str:
    p = PROFILES[depth]
    (w0, w1), (s0, s1) = p.words, p.sections
    return f"Depth: {depth.value} — {w0}–{w1} words, {s0}–{s1} sections.\n{p.guidance}"


def sources_block(sources: frozenset[SourceKind], workspace_id: str | None) -> str:
    lines = []
    for kind in SOURCE_ORDER:
        if kind not in sources:
            continue
        text = SOURCE_TEXT[kind]
        if kind is SourceKind.DOCUMENTS and workspace_id is None:
            text = "the demo library"
        lines.append(f"- {kind.value}: {text}")
    denied = [k.value for k in SOURCE_ORDER if k in REQUESTABLE_SOURCES and k not in sources]
    if not lines:
        lines.append("- (no source family is allowed: the researcher has no tools)")
    if denied:
        lines.append(f"Not allowed for this run: {', '.join(denied)}.")
    return "\n".join(lines)


def evidence_block(run: ResearchRun) -> str:
    """Counts only: the Supervisor coordinates, it does not write from sources."""
    if not run.evidence:
        return "(none yet)"
    by_kind = Counter(r.source_kind for r in run.evidence)
    kinds = ", ".join(
        f"{k.value} {by_kind[k]}" for k in (*SOURCE_ORDER, SourceKind.MEMORY) if by_kind[k]
    )
    issued = {r.n for r in run.evidence}
    cited: set[int] = set()
    for e in run.transcript:
        if e.result is not None and e.result.worker == "researcher" and not e.result.failed:
            cited.update(n for n in e.result.evidence_ns if n in issued)
    return f"{len(run.evidence)} evidence chunks ({kinds}). Cited by the findings: {len(cited)}."


def calls_left(budget: CallBudget) -> int:
    """`limit − (real + cached)`, not `remaining()`: cached replies don't consume the budget,
    so `remaining()` would differ between a first run and its cached rerun (plan §3.7)."""
    return max(0, budget.limit - (budget.real + budget.cached))


def budget_line(decisions_left: int, budget: CallBudget) -> str:
    return (
        f"Decisions left: {decisions_left}. LLM calls left: about {calls_left(budget)} "
        "(a researcher task uses up to 5, the other workers 1–2 each)."
    )


def last_hint(decisions_left: int) -> str:
    return " This is your last decision: reply with FINAL." if decisions_left == 1 else ""


def memory_block(run: ResearchRun, fence: DataFence) -> str:
    """Up to `memory_limit` past reports, one fenced block each. `created_at` and
    `reference_titles` are shown when the stored memory carries them."""
    if not run.memories:
        return "(none)"
    blocks = []
    for m in run.memories:
        date = str(m.get("created_at") or "")[:10]
        head = f"Past report ({date}): " if date else "Past report: "
        lines = [head + str(m.get("question", "")), "Summary: " + _cut(str(m.get("summary", "")))]
        titles = m.get("reference_titles") or []
        if isinstance(titles, list | tuple) and titles:
            lines.append("References: " + "; ".join(str(t) for t in titles[:MEMORY_REFERENCES]))
        blocks.append(fence.wrap("\n".join(lines), "memory", run=str(m.get("run_id", ""))))
    return "\n\n".join(blocks)


def _cut(text: str, limit: int = MEMORY_SUMMARY_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


# ---- tool-use summary --------------------------------------------------------------------


def ranges(ns: Iterable[int]) -> str:
    """{4, 5, 6, 9} → 'E4–E6, E9'."""
    out: list[str] = []
    values = sorted(set(ns))
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and values[j + 1] == values[j] + 1:
            j += 1
        a, b = values[i], values[j]
        out.append(f"E{a}" if a == b else f"E{a}–E{b}")
        i = j + 1
    return ", ".join(out)


def tool_summary(records: list[ToolCallRecord], earlier: Iterable[int] = ()) -> str:
    """Pure: counts by tool and status, fallbacks, the cap, up to 3 queries, and the evidence
    numbers new in these records (`earlier` = numbers given out by previous tool calls).
    No durations: they would break the response cache."""
    if not records:
        return "Tools: none used"
    per_tool: dict[str, Counter[str]] = {}
    for r in records:
        per_tool.setdefault(r.tool, Counter())[r.status] += 1
    parts = []
    for tool, statuses in per_tool.items():
        bad = ", ".join(f"{statuses[s]} {s}" for s in STATUS_ORDER if statuses[s])
        parts.append(f"{tool} ×{sum(statuses.values())}" + (f" ({bad})" if bad else ""))
    line = "Tools: " + ", ".join(parts)
    fallbacks = Counter(r.fallback for r in records if r.fallback)
    if fallbacks:
        line += " · fallback: " + ", ".join(f"{name} ×{n}" for name, n in fallbacks.items())
    if any(r.status == "capped" for r in records):
        line += " · cap reached"
    lines = [line]
    queries: list[str] = []
    for r in records:
        q = r.args.get("query")
        if isinstance(q, str) and q.strip():
            q = trim_query(q)
            if q not in queries:
                queries.append(q)
    if queries:
        lines.append("Queries: " + " · ".join(f'"{q}"' for q in queries[:MAX_QUERIES]))
    new = {n for r in records for n in r.evidence_ns} - set(earlier)
    lines.append(
        f"New evidence: {ranges(new)} ({len(new)} chunks)" if new else "New evidence: none"
    )
    return "\n".join(lines)


def trim_query(query: str) -> str:
    q = " ".join(query.split()).replace('"', "'")
    return q if len(q) <= QUERY_CHARS else q[: QUERY_CHARS - 1].rstrip() + "…"


def fold_tool_calls(
    entries: list[TranscriptEntry],
) -> dict[int, tuple[list[ToolCallRecord], frozenset[int]]]:
    """Result seq → (the TOOL_CALL records of that worker in the same iteration since its
    previous result, the evidence numbers given out by earlier tool calls)."""
    pending: dict[str, list[TranscriptEntry]] = {}
    given: set[int] = set()
    out: dict[int, tuple[list[ToolCallRecord], frozenset[int]]] = {}
    for e in entries:
        if e.kind is EntryKind.TOOL_CALL and e.tool is not None:
            pending.setdefault(e.agent, []).append(e)
        elif e.kind is EntryKind.WORKER_RESULT and e.result is not None:
            records = [
                t.tool
                for t in pending.pop(e.agent, [])
                if t.iteration == e.iteration and t.tool is not None
            ]
            out[e.seq] = (records, frozenset(given))
            given.update(n for r in records for n in r.evidence_ns)
    return out


# ---- the transcript ----------------------------------------------------------------------


def _render(
    e: TranscriptEntry,
    fence: DataFence,
    limit: int | None,
    tools: tuple[list[ToolCallRecord], frozenset[int]] | None,
) -> str:
    head = f"[iteration {e.iteration}]"
    if e.kind is EntryKind.DECISION and e.decision is not None:
        d = e.decision
        if d.final is not None:
            return f"{head} You proposed a FINAL report (sent to the human reviewer)."
        moves = "; ".join(f"DELEGATE {x.worker} — TASK: {x.task}" for x in d.delegations)
        return f"{head} You: {moves}"
    if e.kind is EntryKind.WORKER_RESULT and e.result is not None:
        content = e.result.content if limit is None else trim(e.result.content, limit)
        if e.result.worker == "researcher":
            records, earlier = tools or ([], frozenset())
            content = f"{tool_summary(records, earlier)}\n\n{content}"
        status = " (failed)" if e.result.failed else ""
        body = fence.wrap(content, "worker_result", worker=e.result.worker)
        return f"{head} Result from {e.result.worker}{status}:\n{body}"
    if e.kind is EntryKind.FEEDBACK:
        return f"{head} Human reviewer feedback on your FINAL report (revise it):\n" + fence.wrap(
            e.text or "", "human_feedback"
        )
    return f"[note] {e.text}"


def _visible(e: TranscriptEntry) -> bool:
    if e.kind is EntryKind.TOOL_CALL:
        return False
    return not (e.kind is EntryKind.NOTICE and e.flag in TOOL_NOISE)


def transcript_view(run: ResearchRun, fence: DataFence, budget_tokens: int) -> str:
    folded = fold_tool_calls(run.transcript)
    entries = [e for e in run.transcript if _visible(e)]
    if not entries:
        return "(nothing yet: this is your first decision)"
    in_full: set[int] = set()
    for worker in SHOWN_IN_FULL:
        latest = next(
            (
                e.seq
                for e in reversed(entries)
                if e.result is not None and e.result.worker == worker and not e.result.failed
            ),
            None,
        )
        if latest is not None:
            in_full.add(latest)
    limits: dict[int, int | None] = {
        e.seq: None if e.seq in in_full else RESULT_CHARS for e in entries
    }

    def render() -> str:
        return "\n\n".join(_render(e, fence, limits[e.seq], folded.get(e.seq)) for e in entries)

    text = render()
    for e in entries:  # oldest first
        if estimate_tokens(text) <= budget_tokens:
            break
        if e.result is not None and limits[e.seq] is not None:
            limits[e.seq] = MIN_RESULT_CHARS
            text = render()
    return text


# ---- earlier turns of the chat (v1.2, plan Improving_Chat_UI_UX §5.1) ----------------------

COMPACT_TURNS = 5  # the latest finished turns, shown compact
OLDER_QUESTIONS = 10  # older turns: their questions only
CONVERSATION_CHARS = 4000  # ≈ 1.1k tokens for the whole block
TURN_QUESTION_CHARS = 300
TURN_SUMMARY_CHARS = 600
TURN_TITLE_CHARS = 200
TURN_HEADINGS = 8
HEADING_CHARS = 80
OLDER_QUESTION_CHARS = 150
FIRST_TURN = "(this is the first question of the chat)"
SHOWN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED})
# [E3], [e3], [E 3], [E1; E3], [E1, 3]: evidence numbers belong to their own run
_MARKERS = re.compile(r"\s*\[\s*E\s*\d{1,4}(?:\s*[,;]\s*E?\s*\d{1,4})*\s*\]", re.IGNORECASE)


@dataclass(frozen=True)
class ConversationTurn:
    """An earlier turn as the agents see it. Built from a STORED run, never from client
    input (plan §4.1)."""

    run_id: str
    turn: int
    question: str
    status: str
    title: str
    summary: str
    section_headings: tuple[str, ...]
    evidence_count: int
    created_at: str

    @classmethod
    def from_run(cls, run: ResearchRun) -> "ConversationTurn":
        report = run.report
        return cls(
            run_id=run.id,
            turn=run.turn,
            question=run.question,
            status=run.status.value,
            title=report.title if report else "",
            summary=report.summary if report else "",
            section_headings=tuple(s.heading for s in report.sections) if report else (),
            evidence_count=len(run.evidence),
            created_at=run.created_at,
        )


def earlier_turns(conversation: list[ResearchRun], current: ResearchRun) -> list[ConversationTurn]:
    """The finished turns before `current` (completed, or failed with a note), oldest first."""
    return [
        ConversationTurn.from_run(r)
        for r in conversation
        if r.id != current.id and r.turn < current.turn and r.status in SHOWN_STATUSES
    ]


def strip_markers(text: str) -> str:
    return _MARKERS.sub("", text)


def _clip(text: str, limit: int) -> str:
    text = " ".join(strip_markers(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _compact(t: ConversationTurn, fence: DataFence, summary_chars: int) -> str:
    lines = [f"Question: {_clip(t.question, TURN_QUESTION_CHARS)}"]
    if t.status == RunStatus.FAILED.value:
        lines.append("Outcome: this turn failed; it produced no report.")
    else:
        if t.title:
            lines.append(f"Report title: {_clip(t.title, TURN_TITLE_CHARS)}")
        if t.summary and summary_chars > 0:
            lines.append(f"Summary: {_clip(t.summary, summary_chars)}")
        headings = [_clip(h, HEADING_CHARS) for h in t.section_headings[:TURN_HEADINGS]]
        if headings:
            lines.append("Sections: " + "; ".join(h for h in headings if h))
    lines.append(f"Evidence gathered: {t.evidence_count}")
    return fence.wrap("\n".join(lines), "earlier_turn", turn=t.turn)


def _questions(turns: list[ConversationTurn], fence: DataFence) -> str:
    lines = [f"Turn {t.turn}: {_clip(t.question, OLDER_QUESTION_CHARS)}" for t in turns]
    return fence.wrap("\n".join(lines), "earlier_questions")


def conversation_context(
    turns: list[ConversationTurn], fence: DataFence, budget: int = CONVERSATION_CHARS
) -> tuple[str, int]:
    """(the "Earlier turns in this chat" block, how many earlier turns it shows).

    The last COMPACT_TURNS turns compact (one fenced block each), up to OLDER_QUESTIONS older
    ones as one line each. Over budget, it is trimmed oldest first: the oldest compact turn
    becomes a question line, then the oldest question lines go; as a last resort the latest
    turn's summary is shortened. Pure and deterministic (cache-friendly)."""
    if not turns:
        return FIRST_TURN, 0
    turns = sorted(turns, key=lambda t: t.turn)
    compact = turns[-COMPACT_TURNS:]
    older = turns[:-COMPACT_TURNS][-OLDER_QUESTIONS:]
    summary_chars = TURN_SUMMARY_CHARS

    def render() -> str:
        parts = [_questions(older, fence)] if older else []
        parts += [_compact(t, fence, summary_chars) for t in compact]
        return "\n\n".join(parts)

    text = render()
    while len(text) > budget:
        if len(compact) > 1:
            older = [*older, compact.pop(0)][-OLDER_QUESTIONS:]
        elif older:
            older.pop(0)
        elif summary_chars > 0:
            summary_chars = max(0, summary_chars - 200)
        else:
            break
        text = render()
    return text, len(older) + len(compact)


def conversation_block(turns: list[ConversationTurn], fence: DataFence) -> str:
    """BACKEND_PLAN §26.2: the block shown to the Supervisor and the Writer."""
    return conversation_context(turns, fence)[0]
