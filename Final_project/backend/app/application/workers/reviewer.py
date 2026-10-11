"""Reviewer (REVIEWER_PLAN §3.2): judges the latest draft as a report. Deterministic
pre-checks first (length and sections for the depth, title, unknown citations, citation
coverage: 0 calls), then ONE structured call with the checklist, then code merges the issues
and decides the verdict (any high or medium issue → revise). Owner: REVIEWER."""

import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.application.citations import clean_citations, normalize_citations
from app.application.prompts import PromptLibrary
from app.application.report import parse_report
from app.application.structured import StructuredCaller
from app.application.tracing import Tracer
from app.domain.depth import PROFILES, Depth
from app.domain.evidence import EvidenceRef, SourceKind
from app.domain.ports import LLMClient
from app.domain.schemas import Review, ReviewIssue, ReviewLLM
from app.domain.transcript import WorkerResult

from .base import WorkerContext, WorkerOutput, trim
from .fact_checker import NO_DRAFT, paragraphs

MAX_ISSUES = 8
DEPTH_TOLERANCE = 0.2  # outside the word range by more than 20 % → medium
COVERAGE_MIN_WORDS = 25  # paragraphs shorter than this are not counted for coverage
MAX_UNCITED_SECTIONS = 2
RENDER_CHARS = 1200  # the Supervisor's context keeps 1,200 characters of each result
ISSUE_CHARS = 200

_FENCE = re.compile(r"^\s*(```|~~~)")
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass
class ReviewChecks:
    words: int
    word_range: tuple[int, int]
    sections: int
    section_range: tuple[int, int]
    has_title: bool
    unknown: list[int]
    uncited_paragraphs: int
    paragraphs: int
    issues: list[ReviewIssue] = field(default_factory=list)

    def summary(self) -> str:
        (wl, wh), (sl, sh) = self.word_range, self.section_range
        return (
            f"{self.words:,} words ({wl}–{wh}) · {self.sections} sections ({sl}–{sh}) · "
            f"{self.uncited_paragraphs} of {self.paragraphs} paragraphs uncited · "
            f"{len(self.unknown)} unknown citations"
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("issues")
        d["word_range"], d["section_range"] = list(self.word_range), list(self.section_range)
        return d


def _has_title(markdown: str) -> bool:
    in_code = False
    for line in markdown.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
        elif not in_code and line.strip().startswith("# "):
            return True
    return False


def _issue(severity: str, category: str, text: str, section: str | None = None) -> ReviewIssue:
    return ReviewIssue(
        severity=severity, category=category, section=section, text=text, origin="check"
    )


def review_checks(draft: str, depth: Depth, evidence: list[EvidenceRef]) -> ReviewChecks:
    """The deterministic part of the review: 0 calls, issues with origin "check"."""
    profile = PROFILES[depth]
    report = parse_report(draft, evidence, depth=depth)
    valid = {r.n for r in evidence}
    _, _, unknown = clean_citations(draft, valid)
    unknown = list(dict.fromkeys(unknown))
    long_paragraphs = uncited = 0
    cited_by_section: dict[str, int] = {}
    for section, text, _ in paragraphs(normalize_citations(draft)):
        plain, cited, _ = clean_citations(text, valid)
        if section is not None:
            cited_by_section[section] = cited_by_section.get(section, 0) + len(cited)
        if len(plain.split()) >= COVERAGE_MIN_WORDS:
            long_paragraphs += 1
            uncited += not cited
    checks = ReviewChecks(
        words=report.word_count,
        word_range=profile.words,
        sections=len(report.sections),
        section_range=profile.sections,
        has_title=_has_title(draft),
        unknown=unknown,
        uncited_paragraphs=uncited,
        paragraphs=long_paragraphs,
    )
    lo, hi = profile.words
    if not lo <= checks.words <= hi:
        far = checks.words < lo * (1 - DEPTH_TOLERANCE) or checks.words > hi * (1 + DEPTH_TOLERANCE)
        text = f"{checks.words:,} words, target {lo}–{hi}."
        checks.issues.append(_issue("medium" if far else "low", "depth_fit", text))
    slo, shi = profile.sections
    if not slo <= checks.sections <= shi:
        text = f"{checks.sections} sections, target {slo}–{shi}."
        checks.issues.append(_issue("medium", "structure", text))
    if not checks.has_title:
        checks.issues.append(_issue("medium", "structure", "The draft has no title line (# )."))
    if unknown:
        ids = ", ".join(f"E{n}" for n in unknown)
        text = f"Cites {ids}, which the run never issued."
        checks.issues.append(_issue("high", "citation", text))
    empty = [s for s, n in cited_by_section.items() if n == 0][:MAX_UNCITED_SECTIONS]
    for section in empty:
        checks.issues.append(_issue("medium", "citation", "No citation in this section.", section))
    return checks


def merge_issues(checks: list[ReviewIssue], model: list[ReviewIssue]) -> list[ReviewIssue]:
    """Check issues first, then the model's; the same category + section is kept once."""
    out: list[ReviewIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in [*checks, *model]:
        key = (issue.category, (issue.section or "").lower())
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out[:MAX_ISSUES]


def decide(issues: list[ReviewIssue]) -> str:
    """The verdict rule lives in code: any high or medium issue → revise."""
    return "revise" if any(i.severity in ("high", "medium") for i in issues) else "approve"


def evidence_list(evidence: list[EvidenceRef]) -> str:
    def source(ref: EvidenceRef) -> str:
        if ref.source_kind is SourceKind.DOCUMENTS or not ref.url:
            return ref.document_name or ref.source_kind.value
        return (urlparse(ref.url).hostname or ref.url).removeprefix("www.")

    lines = [f"{r.label} · {r.title} · {source(r)}" for r in sorted(evidence, key=lambda r: r.n)]
    return "\n".join(lines)


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_review(review: Review, checks: ReviewChecks | None = None) -> str:
    """Verdict and high issues first: the Supervisor's context keeps RENDER_CHARS."""
    head = [f"Verdict: {review.verdict.upper()}"]
    flags = "; ".join(review.balance_flags) if review.balance_flags else "none"
    tail = [f"Balance: {_cut(flags, 300)}"]
    if checks is not None:
        tail.append(f"Checks: {checks.summary()}")
    issues = sorted(review.issues, key=lambda i: _SEVERITY_RANK[i.severity])
    if not issues:
        return "\n".join([*head, "Issues: none", *tail])
    lines = [*head, "Issues:"]
    for n, i in enumerate(issues):
        where = ", ".join(x for x in (i.severity, i.category, i.section) if x)
        line = f"- ({where}) {_cut(i.text, ISSUE_CHARS)}"
        more = f"- … and {len(issues) - n} more."
        if len("\n".join([*lines, line, more, *tail])) > RENDER_CHARS:
            lines.append(more)
            break
        lines.append(line)
    return "\n".join([*lines, *tail])


class Reviewer:
    name = "reviewer"
    title = "Reviewer"
    description = (
        "reviews the latest draft against the question, the requested depth and the findings "
        "(task fit, structure, unsupported claims, balance, citation coverage, injected "
        "instructions); returns approve or revise with issues"
    )
    needs = frozenset({"researcher", "writer"})

    def __init__(self, prompts: PromptLibrary) -> None:
        self._prompts = prompts

    def run(self, task: str, ctx: WorkerContext, llm: LLMClient) -> WorkerOutput:
        draft = ctx.results.get("writer")
        if draft is None or draft.failed:
            return WorkerOutput(WorkerResult(self.name, NO_DRAFT, failed=True))
        checks = review_checks(draft.content, ctx.depth, ctx.evidence)
        profile = PROFILES[ctx.depth]
        f = ctx.fence
        draft_text = trim(draft.content, ctx.context_chars // 2)
        findings = ctx.results.get("researcher")
        notes_budget = max(1000, ctx.context_chars - len(draft_text))
        guidance = (
            f"{ctx.depth.value}: {profile.guidance}\n"
            f"Target: {profile.words[0]}–{profile.words[1]} words, "
            f"{profile.sections[0]}–{profile.sections[1]} sections."
        )
        recorded = [f"- ({i.severity}, {i.category}) {i.text}" for i in checks.issues]
        user = self._prompts.worker_task(
            "reviewer_task",
            question=f.wrap(ctx.question, "question"),
            task=task,
            guidance=guidance,
            known_evidence=f.wrap(evidence_list(ctx.evidence) or "(none)", "evidence_list"),
            notes=(
                f.wrap(trim(findings.content, notes_budget), "research_notes")
                if findings
                else "(none)"
            ),
            draft=f.wrap(draft_text, "draft"),
            review=f.wrap("\n".join([checks.summary(), *recorded]), "review_checks"),
        )
        result = StructuredCaller(llm, self._prompts, ctx.model or "").call(
            self._prompts.worker_system(self.name, ctx.canary),
            user,
            ReviewLLM,
            Review,
            Tracer("worker.reviewer", ctx.run_id),
            max_output_tokens=3072,
        )
        answer = result.value or result.last_valid
        assert answer is not None  # noqa: S101 — call() raises when nothing was valid
        model_issues = [i.model_copy(update={"origin": "model"}) for i in answer.issues]
        issues = merge_issues(checks.issues, model_issues)
        verdict = decide(issues)
        review = Review(verdict=verdict, issues=issues, balance_flags=answer.balance_flags)
        data = review.model_dump(mode="json") | {
            "checks": checks.to_dict(),
            "verdict_overridden": verdict != answer.verdict,
        }
        return WorkerOutput(WorkerResult(self.name, render_review(review, checks), data))
