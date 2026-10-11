"""Researcher: the tool-using worker (RESEARCHER_PLAN §3). The model makes the research
decisions (which tool, which query, whether to search again, when to stop); the code enforces
every limit: the offered tools, the cap of RESEARCHER_MAX_TOOL_CALLS, argument checks, the
canary, repeats answered from the run-wide memo, citations limited to evidence this task saw,
then one fresh no-tools findings call. Owner: RESEARCHER."""

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from app.application.prompts import PromptLibrary
from app.application.tools.base import ToolMemo, ToolOutput, evidence_range
from app.application.tools.common import parse_evidence_id
from app.domain.depth import Depth
from app.domain.errors import LLMBadResponse
from app.domain.ports import (
    LLMClient,
    LLMRequest,
    LLMResponse,
    ToolCall,
    ToolResult,
    ToolResultsMessage,
    UserMessage,
)
from app.domain.transcript import CallMeta, ToolCallRecord, WorkerResult

from .base import WorkerContext, WorkerOutput, clean_citations, truncation

SEARCH_TOOLS = frozenset(
    {"search_web", "search_wikipedia", "search_documents", "search_conversation"}  # v1.2
)
CAPPED = "CAPPED Not run: the limit of {cap} tool calls per task is reached."
REPEAT = (
    "REPEAT You already made this exact call in this task. Same evidence as before. "
    "Rewrite the query or write your findings."
)
REPEAT_RUN = (
    "REPEAT This exact call was already made earlier in this run. Same evidence as before. "
    "Search for what is still missing, or write your findings."
)
TOOL_FAILED = "ERROR The tool failed. Try another tool or write findings."
BLOCKED = "REFUSED The arguments contain a blocked value. The call was not run."
WORD_LIMITS = {Depth.BRIEF: 250, Depth.DETAILED: 400, Depth.TECHNICAL: 500}
DEPTH_HINTS = {
    Depth.BRIEF: "Cover the main points only. One or two tool calls are usually enough.",
    Depth.DETAILED: "Cover the main points, trade-offs and examples. Usually two or three calls, "
    "each on a different sub-topic.",
    Depth.TECHNICAL: "Prefer official and primary sources, precise terms and concrete details. "
    "Use read_result when a primary source's snippet is too short.",
}
KNOWN_EVIDENCE = 20
_STRIP = "\"'“”‘’?.! "


# ---- helpers (pure; unit-tested) -------------------------------------------------------------


def normalize_query(value: Any) -> str | None:
    """The memo and record form of a query: NFKC, casefolded, whitespace collapsed, wrapping
    quotes and ?.! stripped. None unless 2–300 characters."""
    if not isinstance(value, str):
        return None
    text = " ".join(unicodedata.normalize("NFKC", value).split()).strip(_STRIP).casefold()
    return text if 2 <= len(text) <= 300 else None


def check_args(tool: str, args: dict[str, Any]) -> tuple[dict[str, str] | None, str]:
    """(normalized args, "") or (None, what is wrong). Unknown tools pass through untouched:
    ToolBox refuses them with the list of offered tools."""
    if tool in SEARCH_TOOLS:
        query = normalize_query(args.get("query"))
        if query is None:
            return None, "query must be a string of 2-300 characters"
        return {"query": query}, ""
    if tool == "read_result":
        n = parse_evidence_id(args.get("evidence_id"))
        if n is None:
            return None, 'evidence_id must look like "E3"'
        return {"evidence_id": f"E{n}"}, ""
    return dict(args), ""


def memo_key(tool: str, args: dict[str, str]) -> tuple[str, str]:
    return tool, json.dumps(args, sort_keys=True)


def log_line(i: int, record: ToolCallRecord) -> str:
    """One app-written line per call for the findings prompt (no untrusted text)."""
    arg = record.args.get("query") or record.args.get("evidence_id") or ""
    head = f"{i}. {record.tool} " + (f'"{_quote(arg)}"' if "query" in record.args else arg)
    if record.repeat:
        outcome = "repeat of an earlier call"
    elif record.status == "ok":
        found = f"{len(record.evidence_ns)} evidence, {evidence_range(record.evidence_ns)}"
        outcome = f"{record.results} results, {found}" if record.tool in SEARCH_TOOLS else found
        if record.fallback:
            outcome += f" (from {record.fallback})"
    elif record.status == "error":
        outcome = f"error: {record.error or 'failed'}"
    else:
        outcome = {"empty": "no results", "refused": "refused", "capped": "not run (cap)"}.get(
            record.status, record.status
        )
    return f"{head} → {outcome}"


def _quote(value: str) -> str:
    return str(value).replace('"', "'")[:300]


def known_evidence(ctx: WorkerContext) -> str:
    latest = ctx.evidence[-KNOWN_EVIDENCE:]
    lines = "\n".join(f"[{r.label}] {r.title} ({r.source_kind.value})" for r in latest)
    return ctx.fence.wrap(lines, "known_evidence") if lines else "(none yet)"


def evidence_blocks(content: str, nonce: str) -> dict[int, str]:
    """The fenced evidence blocks of a tool result, by evidence number. The fence escapes
    markers inside untrusted text, so a page can't fake a block."""
    tag = re.escape(f'<data id="{nonce}"')
    close = re.escape(f'</data id="{nonce}">')
    pattern = re.compile(rf'{tag} kind="evidence" ref="E(\d+)".*?{close}', re.DOTALL)
    return {int(m.group(1)): m.group(0) for m in pattern.finditer(content)}


def trim_blocks(blocks: list[str], max_chars: int) -> list[str]:
    """M5's per-source rule: when the blocks exceed the budget, cut each block's text to
    max(400, budget // n) characters, keeping its opening and closing fence lines."""
    if not blocks or sum(len(b) for b in blocks) <= max_chars:
        return blocks
    limit = max(400, max_chars // len(blocks))
    out = []
    for block in blocks:
        head, _, rest = block.partition("\n")
        body, _, tail = rest.rpartition("\n")
        if len(body) > limit:
            body = body[:limit].rsplit(" ", 1)[0] + " …"
        out.append(f"{head}\n{body}\n{tail}")
    return out


def fallback_findings(seen: dict[int, str], title_of: Callable[[int], str]) -> str:
    """Deterministic findings (0 calls) when the model wrote none (§6)."""
    lines = [f"- {title_of(n)} [E{n}]" for n in sorted(seen)]
    return (
        "### Evidence found\n"
        + "\n".join(lines)
        + ("\n\nGaps: the Researcher could not write findings.")
    )


def rewrites_after_weak(records: list[ToolCallRecord]) -> tuple[int, int]:
    """§3.10: of the empty or weak search calls that were not the task's last call, how many
    were followed by a search with a different query or a different tool. (rewritten, weak)"""
    calls = [r for r in records if r.status != "capped"]
    weak = rewritten = 0
    for i, r in enumerate(calls[:-1]):
        if r.tool not in SEARCH_TOOLS or not (r.status == "empty" or r.weak):
            continue
        weak += 1
        nxt = next((c for c in calls[i + 1 :] if c.tool in SEARCH_TOOLS), None)
        if nxt is not None and (nxt.tool != r.tool or nxt.args != r.args):
            rewritten += 1
    return rewritten, weak


# ---- the worker ------------------------------------------------------------------------------


@dataclass
class _Task:
    """The mutable state of ONE Researcher task."""

    used: int = 0
    capped_calls: int = 0
    canary: bool = False


class Researcher:
    name = "researcher"
    title = "Researcher"
    description = (
        "searches the web, Wikipedia and the user's documents (up to 3 tool calls per task, "
        "choosing its own queries) and writes findings citing evidence as [E<n>]; give it "
        "one focused research question per TASK"
    )
    needs: frozenset[str] = frozenset()

    def __init__(self, prompts: PromptLibrary, max_tool_calls: int = 3) -> None:
        self._prompts, self._cap = prompts, max_tool_calls

    def _request(
        self, ctx: WorkerContext, messages: list, tools: list, max_tokens: int = 3072
    ) -> LLMRequest:
        return LLMRequest(
            system=self._prompts.worker_system(self.name, ctx.canary),
            messages=list(messages),  # a snapshot: the loop keeps appending
            tools=tools,
            max_output_tokens=max_tokens,
            thinking_budget=0,
            model=ctx.model,
        )

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        specs = ctx.tools.offered() if ctx.tools else []
        question = ctx.fence.wrap(ctx.question, "question")
        fenced_task = ctx.fence.wrap(task, "task")
        word_limit = WORD_LIMITS.get(ctx.depth, 400)
        if not specs:
            return self._unsourced(task, ctx, llm, question)

        user = self._prompts.worker_task(
            "researcher_task",
            question=question,
            task=fenced_task,
            depth_hint=DEPTH_HINTS.get(ctx.depth, DEPTH_HINTS[Depth.DETAILED]),
            tools="\n".join(f"- {s.name}: {s.description}" for s in specs),
            cap=self._cap,
            known_evidence=known_evidence(ctx),
            word_limit=word_limit,
        )
        messages: list = [UserMessage(user)]
        state = _Task()
        seen: dict[int, str] = {}  # evidence n → its fenced block, as the model saw it
        records: list[ToolCallRecord] = []
        local: set[tuple[str, str]] = set()  # memo keys first made in THIS task
        notices: list[tuple[str, str]] = []
        findings: str | None = None
        while state.used < self._cap:
            response = llm.generate(self._request(ctx, messages, specs))
            notices += truncation(response, self.name)
            if not response.tool_calls:
                findings = (response.text or "").strip() or None
                break  # text: the findings; empty: the findings call below (+1 repair)
            messages.append(response.as_message())
            results = []
            for i, call in enumerate(response.tool_calls):
                out = self._call(call, ctx, state, local)
                records.append(out.record)
                ctx.record_tool_call(out.record, _meta(ctx, response, first=i == 0, out=out))
                for n, block in evidence_blocks(out.content, ctx.fence.nonce).items():
                    seen.setdefault(n, block)
                results.append(ToolResult(call.id, call.name, out.content))
            messages.append(ToolResultsMessage(results))

        forced = findings is None and state.used >= self._cap
        if findings is None:  # cap reached or empty answer: one fresh call without tools
            findings = self._findings_call(ctx, llm, question, fenced_task, records, seen,
                                           word_limit, notices)  # fmt: skip
        if findings is None:
            if not seen:
                raise LLMBadResponse("The Researcher wrote no findings and found no evidence.")
            findings = fallback_findings(seen, lambda n: _title(ctx, n))
            notices.append(("The Researcher wrote no findings: listing the evidence found.",
                            "researcher_fallback"))  # fmt: skip
        return self._result(findings, seen, records, state, forced, notices)

    # ---- one tool call -------------------------------------------------------------------

    def _call(
        self, call: ToolCall, ctx: WorkerContext, state: _Task, local: set[tuple[str, str]]
    ) -> ToolOutput:
        """§3.2: cap → arguments → canary → repeat → run. Never raises for a tool's sake."""
        assert ctx.tools is not None  # noqa: S101 — run() only calls tools when some are offered
        if state.used >= self._cap:
            state.capped_calls += 1
            record = ToolCallRecord(self.name, call.name, _short(call.args), "capped")
            return ToolOutput(CAPPED.format(cap=self._cap), record)
        state.used += 1
        if ctx.canary and ctx.canary in json.dumps(call.args, ensure_ascii=False):
            state.canary = True
            record = ToolCallRecord(self.name, call.name, {}, "refused", error="blocked argument")
            return ToolOutput(BLOCKED, record)
        args, problem = check_args(call.name, call.args)
        if args is None:
            record = ToolCallRecord(self.name, call.name, _short(call.args), "error",
                                    error="invalid arguments")  # fmt: skip
            return ToolOutput(f"ERROR Invalid arguments: {problem}. The call was not run.", record)
        memo: ToolMemo = ctx.tools.memo
        key = memo_key(call.name, args)
        if (earlier := memo.get(key)) is not None:
            text = REPEAT if key in local else REPEAT_RUN
            record = replace(earlier.record, repeat=True, duration_ms=0)
            return ToolOutput(f"{text}\n{earlier.content}", record)
        try:
            out = ctx.tools.call(call.name, args)
        except Exception:  # noqa: BLE001 — a tool must never raise (§10.2); keep the loop alive
            record = ToolCallRecord(self.name, call.name, args, "error", error="tool failed")
            return ToolOutput(TOOL_FAILED, record)
        if out.record.status in ("ok", "empty"):  # errors may be retried: never memoized
            memo[key] = out
            local.add(key)
        return out

    # ---- the findings call and the result ------------------------------------------------

    def _findings_call(
        self,
        ctx: WorkerContext,
        llm: LLMClient,
        question: str,
        task: str,
        records: list[ToolCallRecord],
        seen: dict[int, str],
        word_limit: int,
        notices: list[tuple[str, str]],
    ) -> str | None:
        blocks = trim_blocks([seen[n] for n in sorted(seen)], ctx.context_chars)
        final = self._prompts.worker_task(
            "researcher_findings",
            question=question,
            task=task,
            tool_log="\n".join(log_line(i, r) for i, r in enumerate(records, 1)) or "(no calls)",
            evidence="\n\n".join(blocks) or "(no evidence)",
            word_limit=word_limit,
        )
        response = llm.generate(self._request(ctx, [UserMessage(final)], [], 4096))
        notices += truncation(response, self.name)
        return (response.text or "").strip() or None

    def _result(
        self,
        findings: str,
        seen: dict[int, str],
        records: list[ToolCallRecord],
        state: _Task,
        forced: bool,
        notices: list[tuple[str, str]],
    ) -> WorkerOutput:
        text, cited, unknown = clean_citations(findings, set(seen))
        if unknown:
            notices.append(
                (f"Removed {len(unknown)} citation(s) to unknown evidence.", "unknown_citation")
            )
        if state.canary:
            notices.append(("A tool call carrying a blocked value was refused.", "canary"))
        if state.capped_calls:
            notices.append((f"{state.capped_calls} tool call(s) over the cap were not run.",
                            "cap_reached"))  # fmt: skip
        if not cited:
            notices.append(("The Researcher's findings cite no evidence.", "unsourced"))
        run = [r for r in records if r.status != "capped"]
        tools: dict[str, int] = {}
        for r in run:
            tools[r.tool] = tools.get(r.tool, 0) + 1
        rewrites, _ = rewrites_after_weak(records)
        repeats = sum(r.repeat for r in run)
        fallbacks = sum(r.fallback is not None for r in run)
        used = ", ".join(f"{t} ×{n}" for t, n in tools.items()) or "no tools"
        summary = f"researcher used {used} · {len(seen)} evidence, {len(cited)} cited"
        extras = [(rewrites, "query rewrite"), (repeats, "repeat"), (fallbacks, "fallback")]
        summary += "".join(f" · {n} {label}{'s' if n > 1 else ''}" for n, label in extras if n)
        data = {
            "tool_calls": state.used,
            "capped": forced,
            "tools": tools,
            "queries": [r.args["query"] for r in run if "query" in r.args],
            "fallbacks": fallbacks,
            "repeats": repeats,
            "rewrites": rewrites,
            "evidence_seen": sorted(seen),
            "evidence_cited": list(cited),
            "summary": summary,
        }
        result = WorkerResult(self.name, text, data, cited, unsourced=not cited)
        return WorkerOutput(result, notices)

    def _unsourced(
        self, task: str, ctx: WorkerContext, llm: LLMClient, question: str
    ) -> WorkerOutput:
        user = self._prompts.worker_task(
            "researcher_unsourced", question=question, task=ctx.fence.wrap(task, "task")
        )
        response = llm.generate(self._request(ctx, [UserMessage(user)], []))
        text = (response.text or "").strip()
        if not text:
            raise LLMBadResponse("The model returned an empty answer.")
        notices = truncation(response, self.name) + [
            ("No tool was available: the findings are unsourced.", "unsourced")
        ]
        data = {"tool_calls": 0, "capped": False, "tools": {}, "queries": [], "fallbacks": 0,
                "repeats": 0, "rewrites": 0, "evidence_seen": [], "evidence_cited": [],
                "summary": "researcher had no tools · unsourced notes"}  # fmt: skip
        return WorkerOutput(WorkerResult(self.name, text, data, unsourced=True), notices)


def _meta(ctx: WorkerContext, response: LLMResponse, *, first: bool, out: ToolOutput) -> CallMeta:
    """Display-only (G4): the turn's model call is shown on its FIRST tool call only, so a turn
    with two calls is not counted twice."""
    if not first:
        return CallMeta(ctx.model, duration_ms=out.record.duration_ms)
    real, cached = (0, 1) if response.cached else (1, 0)
    return CallMeta(ctx.model, response.usage, real, cached, out.record.duration_ms)


def _title(ctx: WorkerContext, n: int) -> str:
    ref = ctx.tools.ctx.evidence_by_n(n) if ctx.tools else None
    return " ".join(ref.title.split())[:200] if ref and ref.title else f"Evidence {n}"


def _short(args: dict[str, Any]) -> dict[str, Any]:
    """Raw (invalid) arguments for the record, ≤ 500 characters in total."""
    text = json.dumps(args, ensure_ascii=False, default=str)
    return dict(args) if len(text) <= 500 else {"raw": text[:480] + "…"}
