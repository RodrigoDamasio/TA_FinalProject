"""Helpers shared by the demo agents (Shared)."""

import re

EVIDENCE_REF = re.compile(r'ref="E(\d+)"')
CITE = re.compile(r"\[E(\d{1,4})\]")


def section(prompt: str, heading: str, until: str | None = None) -> str:
    """Text after `# heading`, up to the next top-level heading (or up to `# until`, for
    sections whose content may itself contain '# ' lines, like report drafts)."""
    part = prompt.split(f"# {heading}", 1)
    if len(part) != 2:
        return ""
    end = f"\n# {until}" if until else "\n# "
    return part[1].split(end, 1)[0]


def question(prompt: str, heading: str = "User question") -> str:
    block = section(prompt, heading)
    inner = re.search(r'kind="question">\n(.*?)\n</data', block, re.DOTALL)
    return " ".join((inner.group(1) if inner else block).split())[:120]


def refs_in(text: str) -> list[int]:
    seen: list[int] = []
    for n in [*EVIDENCE_REF.findall(text), *CITE.findall(text)]:
        if int(n) not in seen:
            seen.append(int(n))
    return seen
