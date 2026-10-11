"""Evidence citations, [E<n>] (BACKEND_PLAN §9, v1.1 §24). Models write variants: grouped
`[E1, E3]` / `[E1; E3]`, lower case `[e3]`, a space `[E 3]`. All are normalized to `[E1][E3]`
first, then markers for evidence the caller did not issue are removed."""

import re

CITATION = re.compile(r"\[E(\d{1,4})\]")
_LOOSE = re.compile(r"\[\s*[Ee]\s*(\d{1,4})\s*\]")
_GROUP = re.compile(r"\[\s*([Ee]\s*\d{1,4}(?:\s*[,;]\s*[Ee]?\s*\d{1,4})+)\s*\]")


def normalize_citations(text: str) -> str:
    def group(m: re.Match[str]) -> str:
        numbers = re.findall(r"\d{1,4}", m.group(1))
        return "".join(f"[E{int(n)}]" for n in numbers)

    text = _GROUP.sub(group, text)
    return _LOOSE.sub(lambda m: f"[E{int(m.group(1))}]", text)


def cited_numbers(text: str) -> list[int]:
    """Evidence numbers in order of first citation (after normalization)."""
    seen: list[int] = []
    for n in CITATION.findall(normalize_citations(text)):
        if int(n) not in seen:
            seen.append(int(n))
    return seen


def clean_citations(text: str, valid: set[int]) -> tuple[str, tuple[int, ...], list[int]]:
    """Normalize, then drop [E<n>] markers outside `valid`.
    Returns (text, cited numbers sorted, unknown numbers in order)."""
    cited: list[int] = []
    unknown: list[int] = []

    def keep(m: re.Match[str]) -> str:
        n = int(m.group(1))
        if n in valid:
            if n not in cited:
                cited.append(n)
            return m.group(0)
        unknown.append(n)
        return ""

    out = CITATION.sub(keep, normalize_citations(text))
    return out, tuple(sorted(cited)), unknown
