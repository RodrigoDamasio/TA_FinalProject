"""R1–R35: parse_report, reference_label, the word count and snippets (WRITER_PLAN §3.6, §9)."""

import random

import pytest

from app.application import report as report_module
from app.application.citations import CITATION
from app.application.report import (
    count_words,
    format_snippet,
    has_title,
    parse_report,
    reference_label,
    snippets_for,
    unwrap_fence,
)
from app.domain.depth import Depth
from app.domain.evidence import EvidenceChunk, EvidenceRef, SourceKind
from app.domain.report import Reference

WEB = EvidenceRef(1, "c1", SourceKind.WEB, "RAG guide", "https://a.org/rag", None, None)
DOC = EvidenceRef(2, "c2", SourceKind.DOCUMENTS, "Handbook", None, "handbook.pdf", 4)
WIKI = EvidenceRef(3, "c3", SourceKind.WIKIPEDIA, "Fine-tuning", "https://w.org/FT", None, None)
E5 = EvidenceRef(5, "c5", SourceKind.WEB, "Five", "https://five.org/x", None, None)
EVIDENCE = [WEB, DOC, WIKI, E5]

CANONICAL = (
    "# RAG or fine-tuning\n\nUse RAG for fresh facts [E1].\n\n"
    "## Costs\n\nFine-tuning costs more [E3].\n\n"
    "## When\n\nSee the handbook [E2].\n\n"
    "## Caveats\n\nIt depends."
)


def parse(md, **kw):
    kw.setdefault("question", "Q?")
    return parse_report(md, EVIDENCE, **kw)


def headings(r):
    return [s.heading for s in r.sections]


# ---- structure: title, summary, sections (R1–R17) -----------------------------------------


def test_r1_canonical_report():
    r = parse(CANONICAL)
    assert r.title == "RAG or fine-tuning"
    assert r.summary == "Use RAG for fresh facts [E1]."
    assert headings(r) == ["Costs", "When", "Caveats"]
    assert r.sections[0].content == "Fine-tuning costs more [E3]."


def test_r2_two_summary_paragraphs_are_joined_with_a_blank_line():
    r = parse("# T\n\nFirst para.\n\n\n\nSecond para.\n\n## S\n\nBody.")
    assert r.summary == "First para.\n\nSecond para."


def test_r3_bold_first_line_is_the_title():
    r = parse("**Bold title**\n\nSummary here.\n\n## S\n\nBody.")
    assert r.title == "Bold title" and r.summary == "Summary here."
    assert has_title("**Bold title**\n\nx") and not has_title("plain\n\n## S\n\nx")


def test_r4_no_title_falls_back_to_the_question_then_a_default():
    assert parse("Just text.\n\n## S\n\nBody.", question="  What is RAG?  ").title == (
        "What is RAG?"
    )
    assert parse("Just text.", question="").title == "Research report"
    assert parse("x", question="w " * 300).title == ("w " * 100).strip()  # ≤ 200 chars


def test_r5_text_before_the_title_is_kept_as_a_preamble():
    md = "> **Note:** assembled automatically.\n\n# T\n\nThe answer [E1].\n\n## S\n\nBody."
    r = parse(md)
    assert r.title == "T"
    assert r.summary == "> **Note:** assembled automatically.\n\nThe answer [E1]."


@pytest.mark.parametrize("heading", ["Executive summary", "**Summary**", "TL;DR", "1. Overview"])
def test_r6_a_summary_section_becomes_the_summary(heading):
    r = parse(f"# T\n\n## {heading}\n\nThe gist [E1].\n\n## Details\n\nMore.")
    assert r.summary == "The gist [E1]." and headings(r) == ["Details"]


def test_r7_no_summary_uses_the_first_sentence_of_the_first_section():
    r = parse("# T\n\n## Background\n\n- RAG retrieves at query time [E1]. It is cheap.\n- x")
    assert r.summary == "RAG retrieves at query time [E1]."
    assert headings(r) == ["Background"]
    assert parse("# T\n\n## S\n\nNo end mark").summary == "No end mark"
    assert r.word_count == count_words(r.sections[0].content)  # the copy counts once


def test_r8_only_h3_headings_are_sections():
    r = parse("# T\n\nSummary.\n\n### A\n\nOne.\n\n#### Deep\n\nTwo.\n\n### B\n\nThree.")
    assert headings(r) == ["A", "B"] and "#### Deep" in r.sections[0].content


def test_r8b_h3_inside_h2_sections_stays_content():
    r = parse("# T\n\nSummary.\n\n## A\n\n### Sub\n\nOne.")
    assert headings(r) == ["A"] and r.sections[0].content == "### Sub\n\nOne."


def test_r9_no_headings_gives_summary_and_details():
    r = parse("# T\n\nFirst para.\n\nSecond para.\n\nThird para.")
    assert r.summary == "First para."
    assert headings(r) == ["Details"]
    assert r.sections[0].content == "Second para.\n\nThird para."


def test_r10_single_paragraph_is_only_a_summary():
    r = parse("Only one paragraph [E1].")
    assert r.summary == "Only one paragraph [E1]." and r.sections == () and r.title == "Q?"


@pytest.mark.parametrize("name", ["Sources", "References", "Further reading", "**Works cited:**"])
def test_r11_trailing_sources_sections_are_dropped(name):
    r = parse(CANONICAL + f"\n\n## {name}\n\n- [E1] RAG guide\n\n## Bibliography\n\n- x")
    assert headings(r) == ["Costs", "When", "Caveats"]


@pytest.mark.parametrize("line", ["**Sources:**", "Sources:", "### References", "__Sources__"])
def test_r12_a_sources_line_in_the_last_section_cuts_the_rest(line):
    r = parse(CANONICAL + f"\n\n{line}\n- [E1] RAG guide https://a.org/rag")
    assert r.sections[-1].content == "It depends."


def test_r12b_last_section_with_only_sources_is_dropped():
    r = parse("# T\n\nS.\n\n## A\n\nOne.\n\n## Notes\n\n**Sources:**\n- x")
    assert headings(r) == ["A"]


def test_r13_a_sources_section_in_the_middle_is_kept():
    r = parse("# T\n\nS.\n\n## Sources\n\nHow sources were chosen.\n\n## Results\n\nR.")
    assert headings(r) == ["Sources", "Results"]


def test_r14_headings_inside_code_blocks_do_not_count():
    md = "```bash\n# not a title\n## not a section\n```\n\n# Real\n\nSum.\n\n## S\n\n~~~\n## x\n~~~"
    r = parse(md)
    assert r.title == "Real" and headings(r) == ["S"]
    assert r.summary.startswith("```bash\n# not a title")
    assert r.sections[0].content == "~~~\n## x\n~~~"


def test_r14b_unclosed_code_block_runs_to_the_end():
    r = parse("# T\n\nSum.\n\n## A\n\n```\n## inside\n")
    assert headings(r) == ["A"]


def test_r15_a_second_h1_becomes_a_section():
    r = parse("# T\n\nSum.\n\n## A\n\nOne.\n\n# B\n\nTwo.")
    assert r.title == "T" and headings(r) == ["A", "B"]
    r = parse("# T\n\nSum.\n\n# B\n\nTwo.")  # no ## at all
    assert headings(r) == ["B"]


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("1. Background:", "Background"),
        ("2) **Costs** [E1]", "Costs"),
        ("IV. Limits ##", "Limits"),
        ("1.2. Setup", "Setup"),
        ("2024 outlook", "2024 outlook"),
        ("[E1]", "Section 1"),
    ],
)
def test_r16_section_headings_are_cleaned(raw, clean):
    assert headings(parse(f"# T\n\nS.\n\n## {raw}\n\nBody."))[0] == clean


def test_r17_a_heading_without_content_is_dropped():
    r = parse("# T\n\nS.\n\n## Empty\n\n## Full\n\nBody.")
    assert headings(r) == ["Full"]


def test_section_heading_before_any_title_means_no_title():
    r = parse("## First\n\nOne.\n\n# Late\n\nTwo.", question="Q")
    assert r.title == "Q" and headings(r) == ["First", "Late"]


# ---- citations, links and references (R18–R23, R29–R31) -------------------------------------


def test_r18_grouped_citations_are_normalized():
    r = parse("# T\n\nA [E1, E3] and [E1][E3] and [e2].")
    assert r.summary == "A [E1][E3] and [E1][E3] and [E2]."
    assert [x.id for x in r.references] == ["E1", "E3", "E2"]


def test_r19_unknown_citations_are_removed():
    r = parse("# T\n\nA [E9] b [E1].")
    assert "[E9]" not in r.summary and [x.id for x in r.references] == ["E1"]


def test_r20_references_follow_the_order_of_first_citation():
    r = parse("# T\n\nSummary [E5].\n\n## A\n\nOne [E2] two [E5].")
    assert [x.id for x in r.references] == ["E5", "E2"]


def test_r21_citations_are_stripped_from_the_title():
    r = parse("# RAG *explained* [E1]\n\nS.")
    assert r.title == "RAG explained" and r.references == ()


def test_r22_images_are_removed():
    r = parse("# T\n\nSee ![x](https://evil.example/a.png?d=secret) this [E1].")
    assert "evil" not in r.summary and r.summary == "See  this [E1]."


def test_r23_only_links_to_evidence_urls_are_kept():
    r = parse("# T\n\nRead [the guide](https://a.org/rag) or [this](https://evil.example).")
    assert r.summary == "Read [the guide](https://a.org/rag) or this."


def test_r29_reference_titles_fall_back():
    bare_web = EvidenceRef(1, "c", SourceKind.WEB, " ", "https://www.site.org/p", None, None)
    bare_doc = EvidenceRef(2, "d", SourceKind.DOCUMENTS, "", None, "notes.pdf", None)
    nothing = EvidenceRef(3, "e", SourceKind.WEB, "", None, None, None)
    bad_url = EvidenceRef(4, "f", SourceKind.WEB, "", "http://[bad", None, None)
    r = parse_report("A [E1] b [E2] c [E3] d [E4].", [bare_web, bare_doc, nothing, bad_url])
    assert [x.title for x in r.references] == [
        "site.org",
        "notes.pdf",
        "Untitled source",
        "Untitled source",
    ]


def test_r30_snippets():
    long = "word " * 100
    r = parse("# T\n\nA [E1] b [E2] c [E3].", snippets={1: "  a\n b  ", 3: long})
    assert [x.snippet for x in r.references] == ["a b", "", format_snippet(long)]
    cut = r.references[2].snippet
    assert cut.endswith("word…") and len(cut) <= 300
    assert format_snippet("x" * 500) == "x" * 300  # no word boundary: hard cut


def test_r31_reference_labels():
    def ref(kind, title, url=None, name=None, page=None):
        return Reference("E2", title, url, name, page, kind, "")

    assert reference_label(ref(SourceKind.WEB, "RAG", "https://e.com/rag")) == (
        "[E2] RAG — https://e.com/rag"
    )
    assert reference_label(ref(SourceKind.WIKIPEDIA, "Vector database", "https://w/V")) == (
        "[E2] Vector database (Wikipedia) — https://w/V"
    )
    assert reference_label(ref(SourceKind.DOCUMENTS, "Handbook", None, "handbook.pdf", 4)) == (
        "[E2] handbook.pdf, p. 4"
    )
    assert reference_label(ref(SourceKind.DOCUMENTS, "Setup guide", None, "h.pdf", 4)) == (
        "[E2] h.pdf, p. 4 — Setup guide"
    )
    assert reference_label(ref(SourceKind.DOCUMENTS, "notes.md", None, "notes.md")) == (
        "[E2] notes.md"
    )
    assert reference_label(ref(SourceKind.DOCUMENTS, "Notes")) == "[E2] Notes"
    assert reference_label(ref(SourceKind.MEMORY, "Earlier run")) == "[E2] Earlier run"


# ---- normalization (R24–R27) -------------------------------------------------------------------


def test_r24_leaked_data_markers_are_removed():
    md = '<data id="abc123" kind="evidence" ref="E1">\n# T\n\nS [E1].\n</data id="abc123">'
    r = parse(md)
    assert r.title == "T" and r.summary == "S [E1]."


@pytest.mark.parametrize("fence", ["```markdown", "```", "~~~md", "````md"])
def test_r25_a_whole_document_fence_is_unwrapped(fence):
    close = fence.rstrip("markdown").rstrip("md") if fence[0] == "`" else "~~~"
    r = parse(f"{fence}\n{CANONICAL}\n{close}")
    assert r.title == "RAG or fine-tuning" and len(r.sections) == 3
    assert unwrap_fence("text") == "text"


def test_r26_crlf_bom_and_trailing_spaces_give_the_same_result():
    messy = "﻿" + CANONICAL.replace("\n", "  \r\n") + "\r\n"
    assert parse(messy) == parse(CANONICAL)
    assert parse(CANONICAL.replace("\n", "\r")) == parse(CANONICAL)


def test_r27_long_summary_is_cut_at_a_sentence_end():
    sentence = "This sentence has exactly forty-four chars. "
    r = parse("# T\n\n" + sentence * 45 + "\n\n## S\n\nBody.")
    assert len(r.summary) <= 1200 and r.summary.endswith("chars.")
    r = parse("# T\n\n" + "word " * 400)  # no sentence end at all
    assert len(r.summary) <= 1200 and r.summary.endswith("…")


# ---- word count (R28) ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "words"),
    [
        ("Don't use state-of-the-art tools [E1][E2].", 4),
        ("See https://example.com/a?b=c now", 2),
        ("Read [the guide](https://a.org) today", 4),
        ("- one\n- two\n* three\n1. four", 4),
        ("| a | b |\n|---|:---:|\n| c | d |", 4),
        ("> quoted **bold** _it_ `code`", 4),
        ("```python\nx = 1\n```", 2),
        ("![img](https://x/y.png) word", 1),
        ("", 0),
    ],
)
def test_r28_word_count(text, words):
    assert count_words(text) == words


def test_r28b_headings_are_not_counted():
    r = parse("# Title words here\n\nOne two [E1].\n\n## Heading words\n\nThree four.")
    assert r.word_count == 4


# ---- safety (R32–R35) -------------------------------------------------------------------------


@pytest.mark.parametrize("md", ["", None, "   \n\t  \n"])
def test_r32_empty_inputs_give_a_minimal_report(md):
    r = parse_report(md, EVIDENCE, question="My question?")
    assert r.title == "My question?" and r.summary == "" and r.sections == ()
    assert r.references == () and r.word_count == 0


def test_r33_any_internal_error_gives_the_safety_net(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise ValueError("secret text that must not be logged")

    monkeypatch.setattr(report_module, "_find_title", boom)
    r = parse_report("# T\n\nBody [E1] and [E9].", EVIDENCE, question="Q", depth=Depth.BRIEF)
    assert r.title == "Q" and r.depth is Depth.BRIEF
    assert r.sections[0].heading == "Report" and "[E9]" not in r.sections[0].content
    assert r.summary.startswith("# T") and [x.id for x in r.references] == ["E1"]
    assert r.word_count == 3
    assert "ValueError" in caplog.text and "secret text" not in caplog.text
    assert not has_title("# T")


def test_r33b_safety_net_survives_a_failing_normalizer(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(report_module, "_normalize", boom)
    monkeypatch.setattr(report_module, "clean_citations", boom)
    r = parse_report("Body [E1].", EVIDENCE)
    assert r.sections[0].content == "Body ." and r.references == ()


def test_r34_fuzz_never_raises_and_references_are_cited():
    rng = random.Random(1234)  # noqa: S311 — reproducible fuzz, not crypto
    atoms = [
        "# ", "## ", "### ", "#### ", "**", "__", "`", "```", "~~~", "\n", "\n\n", "\r\n",
        " ", "[E1]", "[E2, E3]", "[e5]", "[E9]", "[E 1]", "Sources", "Summary", ":", "1. ",
        "- ", "|", "---", "word", "sentence.", "![x](http://e/a.png)", "[t](https://a.org/rag)",
        '<data id="n">', "</data id=\"n\">", "﻿", "é", "don't", "\t",
    ]  # fmt: skip
    for _ in range(400):
        md = "".join(rng.choice(atoms) for _ in range(rng.randint(0, 60)))
        r = parse_report(md, EVIDENCE, question=rng.choice(["", "Q?"]))
        body = "\n".join([r.summary, *(s.content for s in r.sections)])
        cited = {f"E{n}" for n in CITATION.findall(body)}
        assert {x.id for x in r.references} <= cited
        assert r.title and r.word_count >= 0 and len(r.summary) <= 1200


@pytest.mark.parametrize("depth", list(Depth))
def test_r35_depth_is_passed_through(depth):
    assert parse(CANONICAL, depth=depth).depth is depth


# ---- snippets_for -------------------------------------------------------------------------------


def chunk(cid: str, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        cid, "run:r", SourceKind.WEB, "u", "t", "u", None, None, None, None, 0, text, "h", "t"
    )


def test_snippets_for_loads_only_cited_chunks_in_one_call():
    calls: list[list[str]] = []

    def get(ids):
        calls.append(ids)
        return [chunk("c1", "one\n two"), chunk("c3", "word " * 100)]

    out = snippets_for(EVIDENCE, [3, 1, 3, 42], get)
    assert calls == [["c3", "c1"]]
    assert out[1] == "one two" and out[3].endswith("…")
    assert snippets_for(EVIDENCE, [42], get) == {} and len(calls) == 1


def test_snippets_for_errors_give_empty_snippets():
    def get(ids):
        raise RuntimeError("store down")

    assert snippets_for(EVIDENCE, [1], get) == {}
