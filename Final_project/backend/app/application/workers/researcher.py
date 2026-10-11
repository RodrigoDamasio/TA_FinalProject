"""Researcher: the tool-using worker (step 3 version). The model chooses tools and queries;
the code enforces the offered tools, the cap of RESEARCHER_MAX_TOOL_CALLS, then one
no-tools findings call. RESEARCHER_PLAN adds argument checks, the repeat memo, weak-result
metrics and the final prompts. Owner: RESEARCHER."""

from app.application.prompts import PromptLibrary
from app.domain.ports import LLMClient, LLMRequest, ToolResult, ToolResultsMessage, UserMessage
from app.domain.transcript import CallMeta, ToolCallRecord, WorkerResult

from .base import WorkerContext, WorkerOutput, clean_citations, text_of, truncation

CAPPED = "CAPPED Not run: the limit of {cap} tool calls per task is reached."


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

    def _request(self, ctx: WorkerContext, messages: list, tools: list) -> LLMRequest:
        return LLMRequest(
            system=self._prompts.worker_system(self.name, ctx.canary),
            messages=messages,
            tools=tools,
            max_output_tokens=3072,
            thinking_budget=0,
            model=ctx.model,
        )

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        specs = ctx.tools.offered() if ctx.tools else []
        question = ctx.fence.wrap(ctx.question, "question")
        if not specs:
            user = self._prompts.worker_task("researcher_unsourced", question=question, task=task)
            response = llm.generate(self._request(ctx, [UserMessage(user)], []))
            notices = truncation(response, self.name) + [
                ("No tool was available: the findings are unsourced.", "unsourced")
            ]
            result = WorkerResult(self.name, text_of(response), unsourced=True)
            return WorkerOutput(result, notices)

        known = (
            "\n".join(f"[{r.label}] {r.title} ({r.source_kind.value})" for r in ctx.evidence[:20])
            or "(none yet)"
        )
        user = self._prompts.worker_task(
            "researcher_task",
            question=question,
            task=task,
            tools="\n".join(f"- {s.name}: {s.description}" for s in specs),
            cap=self._cap,
            known_evidence=ctx.fence.wrap(known, "known_evidence"),
        )
        messages: list = [UserMessage(user)]
        used, seen, blocks, records = 0, set[int](), list[str](), list[ToolCallRecord]()
        notices: list[tuple[str, str]] = []
        findings: str | None = None
        while used < self._cap:
            response = llm.generate(self._request(ctx, messages, specs))
            notices += truncation(response, self.name)
            if not response.tool_calls:
                findings = (response.text or "").strip() or None
                break
            messages.append(response.as_message())
            results = []
            for call in response.tool_calls:
                if used >= self._cap:
                    record = ToolCallRecord(self.name, call.name, {}, "capped")
                    content = CAPPED.format(cap=self._cap)
                else:
                    used += 1
                    out = ctx.tools.call(call.name, call.args)  # type: ignore[union-attr]
                    record, content = out.record, out.content
                    blocks.append(content)
                    seen.update(record.evidence_ns)
                records.append(record)
                ctx.record_tool_call(record, CallMeta(ctx.model, duration_ms=record.duration_ms))
                results.append(ToolResult(call.id, call.name, content))
            messages.append(ToolResultsMessage(results))
        if findings is None:  # cap reached or empty answer: one call without tools
            final = self._prompts.worker_task(
                "researcher_findings",
                question=question,
                task=task,
                evidence="\n\n".join(blocks) or "(no evidence)",
            )
            response = llm.generate(self._request(ctx, [UserMessage(final)], []))
            notices += truncation(response, self.name)
            findings = text_of(response)
        text, cited, unknown = clean_citations(findings, seen)
        if unknown:
            notices.append(
                (f"Removed {len(unknown)} citation(s) to unknown evidence.", "unknown_citation")
            )
        tools: dict[str, int] = {}
        for r in records:
            tools[r.tool] = tools.get(r.tool, 0) + 1
        summary = ", ".join(f"{t} ×{n}" for t, n in tools.items()) or "no tools"
        data = {
            "tool_calls": used,
            "capped": used >= self._cap,
            "tools": tools,
            "queries": [r.args.get("query") for r in records if "query" in r.args],
            "evidence_seen": sorted(seen),
            "evidence_cited": list(cited),
            "summary": f"researcher used {summary} · {len(seen)} evidence, {len(cited)} cited",
        }
        result = WorkerResult(self.name, text, data, cited, unsourced=not cited)
        return WorkerOutput(result, notices)
