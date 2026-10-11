"""Demo Researcher (RESEARCHER_PLAN G6): calls the first offered search tool with the
question; when a result says `results=0`, tries the next search tool; then writes findings
citing the first evidence ids it received. Owner: RESEARCHER."""

from app.domain.ports import LLMRequest, ToolCall, ToolResultsMessage

from .common import question, refs_in

PREFERRED = ("search_web", "search_documents", "search_wikipedia")


def respond(request: LLMRequest, prompt: str) -> tuple[str | None, list[ToolCall]]:
    results = [r for m in request.messages if isinstance(m, ToolResultsMessage) for r in m.results]
    if request.tools:
        names = [t.name for t in request.tools]
        searches = [n for n in PREFERRED if n in names] or names
        tried = [r.name for r in results]
        last_empty = bool(results) and "results=0 " in results[-1].content
        untried = [n for n in searches if n not in tried]
        if (not results or last_empty) and untried:
            query = question(prompt, "User question (context)") or "research topic"
            return None, [ToolCall(f"demo-call-{len(results) + 1}", untried[0], {"query": query})]
    refs = refs_in("\n".join(r.content for r in results) or prompt)[:3]
    if not refs:
        return (
            "UNSOURCED: notes from general knowledge, not verified against sources.\n"
            "- Demo mode: no evidence was found for this assignment.\nGaps: everything."
        ), []
    lines = [f"- A relevant fact from the evidence [E{n}]." for n in refs]
    return "### Findings\n" + "\n".join(lines) + "\n\nGaps: none (demo mode).", []
