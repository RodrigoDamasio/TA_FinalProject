"""Report depth (BACKEND_PLAN §5.7). The numbers are the contract; the guidance texts come
from WRITER_PLAN §3.2 and are shown to both the Supervisor and the Writer."""

from dataclasses import dataclass
from enum import StrEnum


class Depth(StrEnum):
    BRIEF = "brief"
    DETAILED = "detailed"
    TECHNICAL = "technical"


@dataclass(frozen=True)
class DepthProfile:
    words: tuple[int, int]  # target range for the report body
    sections: tuple[int, int]
    default_max_iterations: int
    guidance: str


PROFILES: dict[Depth, DepthProfile] = {
    Depth.BRIEF: DepthProfile(
        (200, 400),
        (2, 3),
        4,
        "A short briefing for a busy reader. Lead with the direct answer, keep only the points "
        "that change the reader's understanding or decision, one idea per paragraph, no "
        "background the question did not ask for, and no tables. Prefer plain words to jargon.",
    ),
    Depth.DETAILED: DepthProfile(
        (600, 1000),
        (4, 6),
        5,
        "A balanced overview for an informed non-specialist. Answer first, then explain how and "
        "why, compare the main options or viewpoints with their trade-offs, give concrete "
        "examples, and end with practical implications or open questions. Use a table when it "
        "compares three or more items.",
    ),
    Depth.TECHNICAL: DepthProfile(
        (800, 1400),
        (5, 7),
        6,
        "A precise report for practitioners. Use exact terms and define each once, explain "
        "mechanisms, give parameters, versions and numbers with units, include short code or "
        "configuration examples when the evidence contains them, prefer official "
        "documentation, specifications and papers over secondary sources, and state "
        "limitations and edge cases.",
    ),
}
