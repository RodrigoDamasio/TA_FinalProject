"""REVIEWER: the Reviewer's deterministic pre-checks, merge, verdict rule and rendering
(REVIEWER_PLAN §3.2)."""

from app.application.workers.reviewer import (
    decide,
    evidence_list,
    merge_issues,
    render_review,
    review_checks,
)
from app.domain.depth import Depth
from app.domain.evidence import EvidenceRef, SourceKind
from app.domain.schemas import Review, ReviewIssue

EVIDENCE = [
    EvidenceRef(2, "c2", SourceKind.DOCUMENTS, "Guide", None, "guide.pdf", 3),
    EvidenceRef(1, "c1", SourceKind.WEB, "Page", "https://www.a.org/x", None, None),
    EvidenceRef(3, "c3", SourceKind.WIKIPEDIA, "Wiki", None, None, None),
]


def words(n: int, cite: str = " [E1]") -> str:
    return " ".join(["word"] * n) + cite + "."


def draft(per_section: list[int], title: bool = True, cite: str = " [E1]") -> str:
    head = "# Title\n\n" if title else ""
    body = "\n\n".join(f"## S{i}\n\n{words(n, cite)}" for i, n in enumerate(per_section))
    return f"{head}{words(30)}\n\n{body}"


def kinds(checks) -> list[tuple[str, str, str | None]]:
    return [(i.severity, i.category, i.section) for i in checks.issues]


def test_a_draft_inside_every_range_has_no_check_issue():
    checks = review_checks(draft([100, 100]), Depth.BRIEF, EVIDENCE)
    assert checks.words == 230 and checks.sections == 2 and checks.has_title
    assert checks.issues == []
    assert checks.paragraphs == 3 and checks.uncited_paragraphs == 0 and checks.unknown == []
    assert checks.summary() == (
        "230 words (200–400) · 2 sections (2–3) · 0 of 3 paragraphs uncited · 0 unknown citations"
    )
    assert checks.to_dict()["word_range"] == [200, 400] and "issues" not in checks.to_dict()


def test_word_ranges_low_and_medium():
    assert kinds(review_checks(draft([70, 70]), Depth.BRIEF, EVIDENCE)) == [
        ("low", "depth_fit", None)  # 170 words: under 200 but within 20 %
    ]
    assert kinds(review_checks(draft([20, 20]), Depth.BRIEF, EVIDENCE)) == [
        ("medium", "depth_fit", None)  # 70 words
    ]
    checks = review_checks(draft([200, 200, 200]), Depth.BRIEF, EVIDENCE)
    assert ("medium", "depth_fit", None) in kinds(checks)  # 630 > 480


def test_sections_title_unknown_and_uncited_sections():
    checks = review_checks(draft([300, 300, 300], title=False), Depth.DETAILED, EVIDENCE)
    assert kinds(checks) == [  # 930 words: inside 600–1000
        ("medium", "structure", None),  # 3 sections, target 4–6
        ("medium", "structure", None),  # no title
    ]
    assert not checks.has_title
    fenced = "```\n# not a title\n```\n\n" + draft([100], title=False)
    assert not review_checks(fenced, Depth.BRIEF, EVIDENCE).has_title

    bad = draft([100, 100]) + "\n\nMore " + words(5, " [E9][E9][E7]")
    checks = review_checks(bad, Depth.BRIEF, EVIDENCE)
    assert checks.unknown == [9, 7]
    assert ("high", "citation", None) in kinds(checks)
    assert any(i.text == "Cites E9, E7, which the run never issued." for i in checks.issues)

    uncited = draft([100, 100, 100], cite="")
    checks = review_checks(uncited, Depth.BRIEF, EVIDENCE)
    assert [k for k in kinds(checks) if k[1] == "citation"] == [
        ("medium", "citation", "S0"),
        ("medium", "citation", "S1"),  # at most 2 such issues
    ]
    assert checks.uncited_paragraphs == 3 and checks.paragraphs == 4
    assert all(i.origin == "check" for i in checks.issues)


def issue(severity: str, category: str, section: str | None = None, text: str = "x"):
    return ReviewIssue(severity=severity, category=category, section=section, text=text)


def test_merge_dedups_by_category_and_section_and_caps():
    checks = [issue("medium", "depth_fit"), issue("medium", "citation", "Costs")]
    model = [
        issue("low", "depth_fit"),  # same as a check issue: dropped
        issue("high", "citation", "costs"),  # same section, other case: dropped
        issue("high", "citation", "Setup"),
        *[issue("low", "balance", f"S{i}") for i in range(10)],
    ]
    merged = merge_issues(checks, model)
    assert len(merged) == 8
    assert merged[:3] == [checks[0], checks[1], model[2]]


def test_verdict_rule():
    assert decide([]) == "approve"
    assert decide([issue("low", "balance")]) == "approve"
    assert decide([issue("medium", "structure")]) == "revise"
    assert decide([issue("high", "injection")]) == "revise"


def test_evidence_list_is_sorted_titles_only():
    assert evidence_list(EVIDENCE).splitlines() == [
        "E1 · Page · a.org",
        "E2 · Guide · guide.pdf",
        "E3 · Wiki · wikipedia",
    ]


def test_render_puts_the_verdict_and_high_issues_first():
    checks = review_checks(draft([60, 60]), Depth.BRIEF, EVIDENCE)
    review = Review(
        verdict="revise",
        issues=[
            issue("medium", "depth_fit", text="1,310 words, target 600–1000."),
            issue("high", "unsupported_claim", "Costs", "The price is not in the findings."),
        ],
        balance_flags=['"clearly superior" is loaded wording'],
    )
    text = render_review(review, checks)
    assert text.splitlines()[:4] == [
        "Verdict: REVISE",
        "Issues:",
        "- (high, unsupported_claim, Costs) The price is not in the findings.",
        "- (medium, depth_fit) 1,310 words, target 600–1000.",
    ]
    assert 'Balance: "clearly superior" is loaded wording' in text
    assert text.splitlines()[-1].startswith("Checks: 150 words (200–400)")
    clean = render_review(Review(verdict="approve", issues=[], balance_flags=[]))
    assert clean == "Verdict: APPROVE\nIssues: none\nBalance: none"


def test_render_stays_within_the_context_trim():
    many = [issue("low", "balance", f"Section {i}", "t" * 400) for i in range(8)]
    review = Review(verdict="approve", issues=many, balance_flags=["f" * 120] * 5)
    text = render_review(review, review_checks(draft([60]), Depth.BRIEF, EVIDENCE))
    assert len(text) <= 1200 and "- … and" in text and text.splitlines()[-1].startswith("Checks")
