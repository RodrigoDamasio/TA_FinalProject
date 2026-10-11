"""Citations [E<n>] (BACKEND_PLAN §9, v1.1) and the report parser (§5.4)."""

from app.application.citations import cited_numbers, clean_citations, normalize_citations
from app.application.report import parse_report, reference_label, snippets_for
from app.domain.depth import Depth
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.report import Reference

WEB = EvidenceRef(1, "c1", SourceKind.WEB, "RAG guide", "https://a.org/rag", None, None)
DOC = EvidenceRef(2, "c2", SourceKind.DOCUMENTS, "Handbook", None, "handbook.pdf", 4)
WIKI = EvidenceRef(3, "c3", SourceKind.WIKIPEDIA, "Fine-tuning", "https://w.org/FT", None, None)


def test_citation_variants_are_normalized():
    assert normalize_citations("a [E1, E3] b [E1; E3] c [e2] d [E 4] e [E5,6]") == (
        "a [E1][E3] b [E1][E3] c [E2] d [E4] e [E5][E6]"
    )
    assert cited_numbers("x [E3] y [E1][E3] z [1]") == [3, 1]  # [1] is not an evidence citation


def test_unknown_citations_are_removed():
    assert clean_citations("a [E1] b [E9] c [e1]", {1}) == ("a [E1] b  c [E1]", (1,), [9])


def test_parse_report_structure_references_and_word_count():
    md = (
        "# RAG or fine-tuning?\n\nUse RAG for fresh facts [E1].\n\n"
        "## Costs\n\nFine-tuning costs more [E3][E1].\n\n## When\n\nSee the handbook [E2, E9].\n\n"
        "## Sources\n- [E1] something"
    )
    r = parse_report(
        md, [WEB, DOC, WIKI], question="Q?", depth=Depth.BRIEF, snippets={1: "s" * 400}
    )
    assert r.title == "RAG or fine-tuning?" and r.summary == "Use RAG for fresh facts [E1]."
    assert [s.heading for s in r.sections] == ["Costs", "When"]  # Sources section dropped
    assert [x.id for x in r.references] == ["E1", "E3", "E2"]  # order of first citation
    assert len(r.references[0].snippet) == 300 and r.depth is Depth.BRIEF
    assert r.word_count == 11  # "Fine-tuning" is one word


def test_parse_report_fallbacks_never_raise():
    r = parse_report("## Only\n\nFirst sentence here. Second one.", [], question="My question?")
    assert r.title == "My question?" and r.summary == "First sentence here."
    empty = parse_report("", [], question="")
    assert empty.title == "Research report" and empty.sections == () and empty.word_count == 0


def test_reference_labels_and_snippets():
    web = Reference("E1", "RAG guide", "https://a.org/rag", None, None, SourceKind.WEB, "")
    doc = Reference("E2", "Handbook", None, "handbook.pdf", 4, SourceKind.DOCUMENTS, "")
    bare = Reference("E5", "Notes", None, None, None, SourceKind.DOCUMENTS, "")
    assert reference_label(web) == "[E1] RAG guide — https://a.org/rag"
    assert reference_label(doc) == "[E2] handbook.pdf, p. 4"
    assert reference_label(bare) == "[E5] Notes"
    chunk = EvidenceChunk(
        "c1", "run:r", SourceKind.WEB, "u", "t", "u", None, None, None, None, 0, "x" * 500, "h", "t"
    )
    assert snippets_for([WEB, DOC], [1, 7], lambda ids: [chunk]) == {1: "x" * 300}
    assert snippets_for([WEB], [], lambda ids: []) == {}
