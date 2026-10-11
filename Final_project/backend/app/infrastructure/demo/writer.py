"""Demo Writer: a report in the final format (# title, summary, ## sections, [E<n>]).
Owner: WRITER."""

from .common import question, refs_in, section


def respond(prompt: str) -> str:
    refs = refs_in(section(prompt, "Evidence") + section(prompt, "Research findings"))[:3]
    marks = "".join(f"[E{n}]" for n in refs)
    first = f"[E{refs[0]}]" if refs else ""
    return (
        f"# {question(prompt) or 'Research report'}\n\n"
        f"Demo summary: the evidence covers the main points of this question {first}.\n\n"
        f"## Overview\n\nDemo draft written from the research findings {marks}.\n\n"
        "## Notes\n\nThis text was written by the demo Writer (no Gemini call)."
    )
