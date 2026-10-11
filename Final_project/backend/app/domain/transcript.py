"""The conversation history of a run: Supervisor decisions, tool calls, worker results, human
feedback and system notices. It is also the run's audit trail (who decided what, with which
model)."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from .ports import TokenUsage


@dataclass(frozen=True)
class Delegation:
    worker: str  # registry key: researcher | writer | reviewer | fact_checker
    task: str


@dataclass(frozen=True)
class Decision:
    delegations: tuple[Delegation, ...]  # empty when the action is FINAL
    final: str | None  # Markdown answer when the action is FINAL
    reason: str | None
    repaired: bool = False  # needed the repair call
    forced: bool = False  # came from the forced-completion call
    preamble_chars: int = 0  # text before the first key (prompt-drift signal)

    @property
    def action(self) -> str:
        return "final" if self.final is not None else "delegate"


@dataclass(frozen=True)
class WorkerResult:
    worker: str
    content: str  # Markdown (Researcher, Writer) or a rendered summary (Reviewer, Fact-Checker)
    data: dict[str, Any] = field(default_factory=dict)  # structured output or {}
    evidence_ns: tuple[int, ...] = ()  # evidence numbers this result cites ([E<n>])
    unsourced: bool = False  # the Researcher fell back to model knowledge
    failed: bool = False  # the worker could not produce a valid result


@dataclass(frozen=True)
class CallMeta:
    """What one step cost: the model, tokens, and real vs cached calls."""

    model: str | None = None
    usage: TokenUsage = TokenUsage()
    real_calls: int = 0
    cached_calls: int = 0
    duration_ms: int = 0

    @property
    def cached(self) -> bool:
        return self.real_calls == 0 and self.cached_calls > 0

    def __add__(self, other: "CallMeta") -> "CallMeta":
        return CallMeta(
            self.model or other.model,
            self.usage + other.usage,
            self.real_calls + other.real_calls,
            self.cached_calls + other.cached_calls,
            self.duration_ms + other.duration_ms,
        )


ToolStatus = Literal["ok", "empty", "error", "refused", "capped"]


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call by a worker (BACKEND_PLAN §5.2, v1.1 §24): what the transcript, the SSE
    `tool_call` event and the tool-use evaluation see."""

    worker: str  # "researcher"
    tool: str  # search_web | search_wikipedia | search_documents | read_result
    args: dict[str, Any]  # NORMALIZED values, ≤ 500 chars in total
    status: ToolStatus
    provider: str | None = None  # "searxng" | "wikipedia" | "documents" | "web-page"
    fallback: str | None = None  # the provider that answered instead (e.g. "wikipedia")
    results: int = 0  # hits returned by the provider
    evidence_ns: tuple[int, ...] = ()  # evidence numbers given to the model
    error: str | None = None  # short reason, never a stack trace
    duration_ms: int = 0
    weak: bool = False  # v1.1: few or off-topic results (rewrite metric)
    repeat: bool = False  # v1.1: the same normalized call again (answered from the memo)


def tool_to_dict(t: ToolCallRecord) -> dict[str, Any]:
    return {
        "worker": t.worker,
        "tool": t.tool,
        "args": dict(t.args),
        "status": t.status,
        "provider": t.provider,
        "fallback": t.fallback,
        "results": t.results,
        "evidence_ns": list(t.evidence_ns),
        "error": t.error,
        "duration_ms": t.duration_ms,
        "weak": t.weak,
        "repeat": t.repeat,
    }


def tool_from_dict(d: dict[str, Any]) -> ToolCallRecord:
    return ToolCallRecord(
        d["worker"],
        d["tool"],
        dict(d["args"]),
        d["status"],
        d["provider"],
        d["fallback"],
        d["results"],
        tuple(d["evidence_ns"]),
        d["error"],
        d["duration_ms"],
        d.get("weak", False),
        d.get("repeat", False),
    )


class EntryKind(StrEnum):
    DECISION = "decision"
    WORKER_RESULT = "worker_result"
    TOOL_CALL = "tool_call"
    FEEDBACK = "feedback"
    NOTICE = "notice"


@dataclass(frozen=True)
class TranscriptEntry:
    seq: int
    iteration: int
    kind: EntryKind
    agent: str  # supervisor | <worker> | human | system
    task: str | None = None  # the TASK given to a worker
    decision: Decision | None = None
    result: WorkerResult | None = None
    text: str | None = None  # feedback or notice text
    flag: str | None = None  # notices: unsourced, forced, repeated, memory, …
    meta: CallMeta = CallMeta()
    tool: ToolCallRecord | None = None  # TOOL_CALL entries


# ---- (de)serialization: state_json and the public view --------------------------------


def _usage(d: dict[str, int]) -> TokenUsage:
    return TokenUsage(d.get("input", 0), d.get("output", 0), d.get("thinking", 0))


def _usage_dict(u: TokenUsage) -> dict[str, int]:
    return {"input": u.input, "output": u.output, "thinking": u.thinking}


def entry_to_dict(e: TranscriptEntry) -> dict[str, Any]:
    """Public and stored shape (it holds no source extracts)."""
    out: dict[str, Any] = {
        "seq": e.seq,
        "iteration": e.iteration,
        "kind": e.kind.value,
        "agent": e.agent,
        "task": e.task,
        "text": e.text,
        "flag": e.flag,
        "model": e.meta.model,
        "tokens": _usage_dict(e.meta.usage),
        "llm_calls": e.meta.real_calls,
        "cached_calls": e.meta.cached_calls,
        "cached": e.meta.cached,
        "duration_ms": e.meta.duration_ms,
        "decision": None,
        "result": None,
        "tool": tool_to_dict(e.tool) if e.tool is not None else None,
    }
    if e.decision is not None:
        d = e.decision
        out["decision"] = {
            "action": d.action,
            "delegations": [{"worker": x.worker, "task": x.task} for x in d.delegations],
            "final": d.final,
            "reason": d.reason,
            "repaired": d.repaired,
            "forced": d.forced,
            "preamble_chars": d.preamble_chars,
        }
    if e.result is not None:
        r = e.result
        out["result"] = {
            "worker": r.worker,
            "content": r.content,
            "data": r.data,
            "evidence_ns": list(r.evidence_ns),
            "unsourced": r.unsourced,
            "failed": r.failed,
        }
    return out


def entry_from_dict(d: dict[str, Any]) -> TranscriptEntry:
    decision = result = None
    if (dd := d.get("decision")) is not None:
        decision = Decision(
            tuple(Delegation(x["worker"], x["task"]) for x in dd["delegations"]),
            dd["final"],
            dd["reason"],
            dd["repaired"],
            dd["forced"],
            dd.get("preamble_chars", 0),
        )
    if (rd := d.get("result")) is not None:
        result = WorkerResult(
            rd["worker"],
            rd["content"],
            rd["data"],
            tuple(rd["evidence_ns"]),
            rd["unsourced"],
            rd["failed"],
        )
    meta = CallMeta(
        d["model"], _usage(d["tokens"]), d["llm_calls"], d["cached_calls"], d["duration_ms"]
    )
    return TranscriptEntry(
        d["seq"],
        d["iteration"],
        EntryKind(d["kind"]),
        d["agent"],
        d["task"],
        decision,
        result,
        d["text"],
        d["flag"],
        meta,
        tool_from_dict(d["tool"]) if d.get("tool") else None,
    )


# ---- public shape: one conversation_history entry (BACKEND_PLAN §14.3) ------------------

DISPLAY_NAMES = {
    "supervisor": "Supervisor",
    "researcher": "Researcher",
    "writer": "Writer",
    "reviewer": "Reviewer",
    "fact_checker": "Fact-Checker",
    "human": "Human",
    "system": "System",
}


def display_name(agent: str) -> str:
    return DISPLAY_NAMES.get(agent, agent.replace("_", " ").title())


def _labels(ns: tuple[int, ...] | list[int]) -> list[str]:
    return [f"E{n}" for n in ns]


def history_entry(e: TranscriptEntry) -> dict[str, Any]:
    """The same dict is the SSE event data and the conversation_history item."""
    out: dict[str, Any] = {
        "seq": e.seq,
        "iteration": e.iteration,
        "agent": display_name(e.agent),
        "model": e.meta.model,
        "llm_calls": e.meta.real_calls,
        "cached": e.meta.cached,
        "duration_ms": e.meta.duration_ms,
        "tokens": _usage_dict(e.meta.usage),
    }
    if e.kind is EntryKind.DECISION and e.decision is not None:
        d = e.decision
        if d.final is not None:
            out |= {"action": "FINAL", "reason": d.reason, "forced": d.forced}
        else:
            first = d.delegations[0] if d.delegations else None
            out |= {
                "action": "DELEGATE",
                "target_agent": display_name(first.worker) if first else None,
                "task": first.task if first else None,
                "delegations": [
                    {"target_agent": display_name(x.worker), "task": x.task} for x in d.delegations
                ],
                "reason": d.reason,
                "repaired": d.repaired,
            }
    elif e.kind is EntryKind.TOOL_CALL and e.tool is not None:
        t = e.tool
        out |= {
            "action": "TOOL_CALL",
            "tool": t.tool,
            "args": dict(t.args),
            "status": t.status,
            "provider": t.provider,
            "fallback": t.fallback,
            "results": t.results,
            "evidence": _labels(t.evidence_ns),
            "error": t.error,
            "weak": t.weak,
            "repeat": t.repeat,
            "duration_ms": t.duration_ms,
        }
    elif e.kind is EntryKind.WORKER_RESULT and e.result is not None:
        r = e.result
        out |= {
            "action": "RESULT",
            "task": e.task,
            "content": r.content,
            "evidence": _labels(r.evidence_ns),
            "data": r.data,
            "failed": r.failed,
            "unsourced": r.unsourced,
        }
    elif e.kind is EntryKind.FEEDBACK:
        out |= {"action": "FEEDBACK", "text": e.text}
    else:
        out |= {"action": "NOTICE", "text": e.text, "flag": e.flag}
    return out
