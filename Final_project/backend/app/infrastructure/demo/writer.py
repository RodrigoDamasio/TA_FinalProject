"""Demo Writer: a report in the final format (# title, summary, ## sections, [E<n>]) that
fits the depth target stated in the prompt, so a fake run passes the Writer's structure check.
Owner: WRITER."""

import math
import re

from .common import question, refs_in, section

HEADINGS = (
    "Overview",
    "Notes",
    "Trade-offs",
    "Practical implications",
    "Open questions",
    "Limitations",
    "Recommendations",
)
_TARGET = re.compile(r"Target: (\d+)\D+(\d+) words in the summary and sections, (\d+)\D+(\d+)")
_FILLER = "In demo mode this sentence stands in for a cited finding about the question"
_FILLER_WORDS = 14


def respond(prompt: str) -> str:
    refs = sorted(refs_in(section(prompt, "Evidence") + section(prompt, "Research findings")))
    refs = refs[:3]
    marks = "".join(f"[E{n}]" for n in refs)
    first = f" [E{refs[0]}]" if refs else ""
    target = _TARGET.search(prompt)
    words_min, sections_min = (int(target.group(1)), int(target.group(3))) if target else (0, 2)
    count = max(2, min(sections_min, len(HEADINGS)))
    per_section = max(0, math.ceil((words_min - 20) / count / _FILLER_WORDS))

    def filler(i: int) -> str:
        cite = f" [E{refs[i % len(refs)]}]" if refs else ""
        return f"{_FILLER}{cite}."

    bodies = [f"Demo draft written from the research findings {marks}.".replace(" .", ".")]
    bodies += ["This text was written by the demo Writer (no Gemini call)."] * (count - 1)
    sections = "\n\n".join(
        f"## {heading}\n\n{body}" + "".join(f" {filler(i + k)}" for k in range(per_section))
        for i, (heading, body) in enumerate(zip(HEADINGS, bodies, strict=False))
    )
    return (
        f"# {question(prompt, 'Question') or 'Research report'}\n\n"
        f"Demo summary: the evidence covers the main points of this question{first}.\n\n"
        f"{sections}"
    )
