"""REVIEWER: the Fact-Checker's pure steps (REVIEWER_PLAN §3.3): extraction, selection,
budget, settling, the claim-id rule, assembly, needs_revision and rendering."""

from app.application.workers.fact_checker import (
    NOTHING_TO_CHECK,
    UNAVAILABLE_NOTE,
    UNKNOWN_NOTE,
    Claim,
    ClaimCandidate,
    Passages,
    assemble,
    claim_ids_check,
    claim_line,
    extract_claims,
    fact_stats,
    fit_budget,
    needs_revision,
    render_fact_check,
    select_claims,
    settle,
    source_of,
    split_sentences,
)
from app.domain.evidence import EvidenceRef, SourceKind
from app.domain.schemas import ClaimVerdict, FactCheck

LONG = "is a sentence long enough to count as a claim"


def test_sentence_split_keeps_abbreviations_and_decimals():
    text = (
        "Use RAG, e.g. for manuals [E1]. Version 3.5 is fast. [E2] Next comes here. "
        "It plays piano. Then No. 5 ran, i.e. it won vs. the rest."
    )
    assert split_sentences(text) == [
        "Use RAG, e.g. for manuals [E1].",
        "Version 3.5 is fast. [E2]",  # the marker opening the next sentence moves back
        "Next comes here.",
        "It plays piano.",
        "Then No. 5 ran, i.e. it won vs. the rest.",
    ]
    assert split_sentences("Ends with a marker.[E3] Then (more) text.") == [
        "Ends with a marker.[E3]",
        "Then (more) text.",
    ]


def test_extract_claims_walks_the_markdown():
    draft = (
        "# Title [E1] that is long enough to be a claim\n\n"
        f"The summary {LONG} [E1]. Short [E2].\n"
        f"It continues on a second line and {LONG} [E1, E3].\n\n"
        "## Costs\n\n"
        f"- **Flash-Lite** {LONG} [E 2].\n"
        f"- Unknown only {LONG} [E9].\n"
        f"| Model | Price {LONG} [E3] |\n|---|---|\n\n"
        "```\ncode [E1] that is long enough to be a claim\n```\n\n"
        f"### Details\n\n> The quoted line {LONG} [e2].\n\n"
        f"The summary {LONG} [E1].\n\n"  # duplicate of the summary sentence
        "## Sources\n\n"
        f"- A source line {LONG} [E1].\n"
    )
    claims = extract_claims(draft, {1, 2, 3})
    assert [(c.section, c.cited, c.unknown) for c in claims] == [
        (None, (1,), ()),
        (None, (1, 3), ()),
        ("Costs", (2,), ()),
        ("Costs", (), (9,)),
        ("Costs", (3,), ()),
        ("Details", (2,), ()),
    ]
    assert claims[0].plain == f"The summary {LONG}."
    assert claims[1].text.endswith("[E1][E3].")  # grouped markers normalized
    assert claims[2].plain.startswith("Flash-Lite ")  # emphasis and list marker dropped
    assert claims[4].text.startswith("Model · Price")  # a table row is one claim
    assert [c.position for c in claims] == list(range(6))
    assert extract_claims("No citations here at all, none whatsoever.", {1}) == []


def _candidate(i: int, section: str | None, digit: bool = False) -> ClaimCandidate:
    plain = f"Claim {'number 7' if digit else 'without numbers'} in {section}"
    return ClaimCandidate(plain + " [E1].", plain, section, i, (1,), ())


def test_selection_round_robins_across_sections_preferring_digits():
    few = [_candidate(i, "A") for i in range(12)]
    assert select_claims(few) == few
    many = [
        _candidate(i * 6 + j, "ABCDE"[i], digit=(j == 5)) for i in range(5) for j in range(6)
    ]  # 30 claims, 5 sections, the last of each section has a digit
    chosen = select_claims(many)
    assert len(chosen) == 12
    assert {c.section for c in chosen} == set("ABCDE")
    assert all(any(c.position == i * 6 + 5 for c in chosen) for i in range(5))  # digits first
    assert [c.position for c in chosen] == sorted(c.position for c in chosen)


def _ref(n: int, kind=SourceKind.WEB, url="https://www.a.org/x", doc=None, page=None):
    return EvidenceRef(n, f"chunk-{n}", kind, f"Title {n}", url, doc, page)


def test_source_and_claim_line():
    assert source_of(_ref(1)) == "a.org"
    assert source_of(_ref(2, SourceKind.DOCUMENTS, None, "guide.pdf", 3)) == "guide.pdf, p. 3"
    assert source_of(_ref(3, SourceKind.DOCUMENTS, None, "notes.md")) == "notes.md"
    c = Claim("C3", ClaimCandidate("It costs $5 [E2].", "It costs $5.", "Costs", 0, (2, 5), ()))
    c.cited, c.elsewhere = [2, 5], [7]
    assert claim_line(c) == (
        "C3 [cites E2, E5] (Costs) It costs $5 [E2]. → check against: E2, E5, E7"
    )


def test_budget_drops_elsewhere_then_same_then_cuts():
    def claims():
        out = []
        for i in range(3):
            c = Claim(f"C{i + 1}", _candidate(i, "A"))
            c.cited, c.same, c.elsewhere = [i * 3 + 1], [i * 3 + 2], [i * 3 + 3]
            out.append(c)
        return out

    texts = {n: "x" * 100 for n in range(1, 10)}
    p = Passages(claims(), dict(texts))
    fit_budget(p, 800)  # 900 → drop one "elsewhere" (the last claim's)
    assert [c.elsewhere for c in p.claims] == [[3], [6], []]
    p = Passages(claims(), dict(texts))
    fit_budget(p, 500)  # every "elsewhere", then "same" from the last claim
    assert [c.elsewhere for c in p.claims] == [[], [], []]
    assert [c.same for c in p.claims] == [[2], [5], []]
    p = Passages(claims(), {n: "x" * 900 for n in range(1, 10)})
    fit_budget(p, 1000)
    assert all(not c.same and not c.elsewhere for c in p.claims)
    assert all(len(t) == 500 for t in p.texts.values())


def test_settle_unknown_and_unavailable():
    unknown = Claim("C1", ClaimCandidate("x [E9].", "x", None, 0, (), (9,)))
    missing = Claim("C2", ClaimCandidate("y [E1].", "y", None, 1, (1,), ()))
    fine = Claim("C3", ClaimCandidate("z [E1].", "z", None, 2, (1,), ()), cited=[1])
    settle([unknown, missing, fine])
    assert unknown.settled.note == UNKNOWN_NOTE and missing.settled.note == UNAVAILABLE_NOTE
    assert unknown.settled.status == "unsupported" and fine.settled is None


def test_claim_id_rule():
    check = claim_ids_check(["C1", "C2"])
    ok = FactCheck.model_validate(
        {"claims": [{"claim": c, "status": "supported"} for c in ("C1", "C2")]}
    )
    assert check(ok) == []
    bad = FactCheck.model_validate(
        {"claims": [{"claim": c, "status": "supported"} for c in ("C1", "C1", "C14")]}
    )
    assert check(bad) == [
        "C14 is not one of the claim ids",
        "C1 appears more than once",
        "missing a verdict for claim C2",
    ]


def test_assemble_miscited_skipped_and_dropped_ids():
    c1 = Claim("C1", ClaimCandidate("a [E1].", "a", "S", 0, (1,), ()), cited=[1], elsewhere=[6])
    c2 = Claim("C2", ClaimCandidate("b [E2].", "b", "S", 1, (2,), ()), cited=[2])
    c3 = Claim("C3", ClaimCandidate("c [E3].", "c", "S", 2, (3,), ()), cited=[3])
    judged = {
        "C1": ClaimVerdict(claim="C1", status="supported", evidence_ids=["E6", "E99"]),
        "C2": ClaimVerdict(claim="C2", status="contradicted", evidence_ids=["E2"], note="No."),
    }
    items = assemble([c1, c2, c3], judged)["claims"]
    assert [i["claim"] for i in items] == ["C1", "C2"]  # C3 has no verdict: skipped
    assert items[0]["evidence_ids"] == ["E6"] and items[0]["miscited"]  # E99 never shown
    assert not items[1]["miscited"] and items[1]["origin"] == "model"
    stats = fact_stats(items, cited_sentences=5, selected=3)
    assert stats == {
        "cited_sentences": 5,
        "checked": 2,
        "skipped": 1,
        "supported": 1,
        "unsupported": 0,
        "contradicted": 1,
        "miscited": 1,
        "supported_ratio": 0.5,
    }


def _stats(checked, unsupported=0, contradicted=0, miscited=0):
    return {
        "checked": checked,
        "unsupported": unsupported,
        "contradicted": contradicted,
        "miscited": miscited,
    }


def test_needs_revision_thresholds():
    assert needs_revision(_stats(12, contradicted=1))
    assert not needs_revision(_stats(12, unsupported=2))  # 2/12 < 25 %
    assert needs_revision(_stats(12, unsupported=2, miscited=1))  # 3/12 = 25 %
    assert not needs_revision(_stats(0))


def _item(i, status, miscited=False, section="Costs"):
    return {
        "claim": f"C{i}",
        "section": section,
        "sentence": f"Sentence {i} " + "words " * 60 + "[E1].",
        "cited": ["E1"],
        "status": status,
        "evidence_ids": ["E6"] if miscited else [],
        "note": "A note.",
        "miscited": miscited,
    }


def test_render_is_worst_first_and_bounded():
    items = (
        [_item(1, "supported"), _item(2, "supported", miscited=True), _item(3, "unsupported")]
        + [_item(4, "contradicted", section=None)]
        + [_item(i, "unsupported") for i in range(5, 13)]
    )
    stats = fact_stats(items, cited_sentences=14, selected=12)
    text = render_fact_check(items, stats, needs_revision(stats))
    lines = text.splitlines()
    assert lines[0].startswith("Fact check: 12 of 14 cited sentences checked: 2 supported")
    assert "1 miscited." in lines[0] and lines[0].endswith("Revision needed.")
    assert lines[1].startswith('- contradicted (Summary): "Sentence 4')
    assert lines[2].startswith("- unsupported (Costs)")
    assert lines[-1].startswith("- … and") and len(text) <= 1200
    small = [_item(1, "supported", miscited=True)]
    stats = fact_stats(small, 1, 2)
    text = render_fact_check(small, stats, False)
    assert "1 skipped. No revision needed." in text
    assert text.splitlines()[1].endswith("Supported by E6, not E1.")
    assert render_fact_check([], fact_stats([], 0, 0), False) == NOTHING_TO_CHECK
