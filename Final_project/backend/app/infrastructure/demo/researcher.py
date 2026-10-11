"""Demo Researcher: calls the first offered search tool with the question, then writes
findings citing the evidence ids it received. Owner: RESEARCHER."""

from app.domain.ports import LLMRequest, ToolCall, ToolResultsMessage

from .common import question, refs_in

PREFERRED = ("search_web", "search_documents", "search_wikipedia")


def respond(request: LLMRequest, prompt: str) -> tuple[str | None, list[ToolCall]]:
    results = [
        r.content for m in request.messages if isinstance(m, ToolResultsMessage) for r in m.results
    ]
    if request.tools and not results:
        names = [t.name for t in request.tools]
        tool = next((n for n in PREFERRED if n in names), names[0])
        query = question(prompt, "User question (context)") or "research topic"
        return None, [ToolCall("demo-call-1", tool, {"query": query})]
    refs = refs_in("\n".join(results) or prompt)[:3]
    if not refs:
        return (
            "UNSOURCED: notes from general knowledge, not verified against sources.\n"
            "- Demo mode: no evidence was found for this assignment.\nGaps: everything."
        ), []
    lines = [f"- A relevant fact from the evidence [E{n}]." for n in refs]
    return "### Findings\n" + "\n".join(lines) + "\n\nGaps: none (demo mode).", []
