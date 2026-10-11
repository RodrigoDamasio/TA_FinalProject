"""ResearchView: the response of every run endpoint and the callback payload body
(BACKEND_PLAN §14.3; moved here from api/ in v1.1 so the outbox dispatcher can render it).
Evidence text is never exposed, only ≤ 300-character reference snippets."""

from typing import Any

from app.domain.run import ResearchRun, RunStatus
from app.domain.transcript import EntryKind, display_name, history_entry

from .report import reference_label
from .stats import Pricing

WORKERS = ("researcher", "writer", "reviewer", "fact_checker")


def _report(run: ResearchRun) -> dict[str, Any] | None:
    if run.status is not RunStatus.COMPLETED or run.report is None:
        return None
    r = run.report
    return {
        "title": r.title,
        "summary": r.summary,
        "sections": [{"heading": s.heading, "content": s.content} for s in r.sections],
        "references": [reference_label(x) for x in r.references],
        "sources": [
            {
                "id": x.id,
                "title": x.title,
                "url": x.url,
                "document_name": x.document_name,
                "page": x.page,
                "source_kind": x.source_kind.value,
                "snippet": x.snippet,
            }
            for x in r.references
        ],
        "depth": r.depth.value,
        "word_count": r.word_count,
    }


def _per_agent(run: ResearchRun) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for e in run.transcript:
        if e.kind is EntryKind.TOOL_CALL or e.agent in ("human", "system"):
            continue  # tool-call metas are display-only (v1.1)
        a = out.setdefault(display_name(e.agent), {"calls": 0, "tokens": 0, "duration_ms": 0})
        a["calls"] += e.meta.real_calls
        a["tokens"] += e.meta.usage.input + e.meta.usage.output + e.meta.usage.thinking
        a["duration_ms"] += e.meta.duration_ms
    return out


def _grounding(run: ResearchRun) -> dict[str, Any] | None:
    check = run.latest_result("fact_checker")
    claims = check.data.get("claims", []) if check else []
    if not claims:
        return None
    counts = {
        s: sum(1 for c in claims if c.get("status") == s)
        for s in ("supported", "unsupported", "contradicted")
    }
    return {"checked": len(claims), **counts, "supported_ratio": counts["supported"] / len(claims)}


def research_view(
    run: ResearchRun, pricing: Pricing, demo: bool, sample: bool = False
) -> dict[str, Any]:
    used = [w for w in WORKERS if any(e.agent == w and e.result for e in run.transcript)]
    duration = run.duration_ms
    return {
        "run_id": run.id,
        "conversation_id": run.conversation_id,
        "turn": run.turn,
        "status": run.status.value,
        "question": run.question,
        "depth": run.depth.value,
        "sources": sorted(s.value for s in run.sources),
        "channel": run.channel.value,
        "report": _report(run),
        "metadata": {
            "iterations": run.iteration,
            "context_turns": run.context_turns,
            "iteration_limit": run.iteration_limit,
            "max_iterations": run.max_iterations,
            "agents_used": [display_name(w) for w in used],
            "duration_seconds": round(duration / 1000, 1) if duration is not None else None,
            "forced": run.forced,
            "degraded": run.degraded,
            "llm_calls": run.llm_calls,
            "cached_calls": run.cached_calls,
            "tool_calls": run.tool_calls,
            "evidence": len(run.evidence),
            "tokens": {
                "input": run.tokens.input,
                "output": run.tokens.output + run.tokens.thinking,
            },
            "est_cost_usd": pricing.cost(run.tokens),
            "per_agent": _per_agent(run),
            "grounding": _grounding(run),
            "models": run.models,
            "prompt_version": run.prompt_version,
            "generated_by": "demo mode (no LLM)" if demo else "AI (Gemini)",
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
        },
        "conversation_history": [history_entry(e) for e in run.transcript],
        "final_output": run.final_output,
        "flags": run.flags,
        "memory_used": [{"run_id": m["run_id"], "question": m["question"]} for m in run.memories],
        "error": run.error,
        "approval": {
            "required": run.require_approval,
            "draft": run.draft,
            "expires_at": run.approval_expires_at,
            "revisions": run.revisions,
        },
        "sample": sample,
    }
