"""Demo Researcher (RESEARCHER_PLAN G6): calls the first offered search tool with the
question; when a result says `results=0`, tries the next search tool; then writes findings
citing the first evidence ids it received. On a follow-up (v1.2) search_conversation is
offered, so it is called first, with the Supervisor's standalone TASK as the query.
Owner: RESEARCHER."""

import re

from app.domain.ports import LLMRequest, ToolCall, ToolResultsMessage

from .common import question, refs_in, section

PREFERRED = ("search_conversation", "search_web", "search_documents", "search_wikipedia")
ASSIGNMENT = "Your assignment from the Supervisor"
_TASK = re.compile(r'kind="task">\n(.*?)\n</data', re.DOTALL)


def task_query(prompt: str) -> str:
    """The fenced TASK as a query (≤ 300 characters), or '' when there is none."""
    found = _TASK.search(section(prompt, ASSIGNMENT))
    return " ".join(found.group(1).split())[:300] if found else ""


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
            if untried[0] == "search_conversation":
                query = task_query(prompt) or query
            return None, [ToolCall(f"demo-call-{len(results) + 1}", untried[0], {"query": query})]
    refs = refs_in("\n".join(r.content for r in results) or prompt)[:3]
    if not refs:
        return (
            "UNSOURCED: notes from general knowledge, not verified against sources.\n"
            "- Demo mode: no evidence was found for this assignment.\nGaps: everything."
        ), []
    lines = [f"- A relevant fact from the evidence [E{n}]." for n in refs]
    return "### Findings\n" + "\n".join(lines) + "\n\nGaps: none (demo mode).", []
