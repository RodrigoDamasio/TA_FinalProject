"""Normalization and chunking (RAG_PLAN §3.1-§3.2)."""

from app.application.indexing.chunkers import (
    ChunkOptions,
    Draft,
    chunk,
    embed_text,
    is_markdown,
    merge_small,
    sections,
    windows,
)
from app.application.indexing.normalize import normalize_text
from app.domain.evidence import EvidenceChunk, SourceKind

OPTS = ChunkOptions(400, 100, 0.15)


def test_normalize_removes_controls_and_folds_lines():
    raw = "a‮b\x00c\r\nline  \r\n\n\n\nnext\x0b\tx"
    assert normalize_text(raw) == "abc\nline\n\nnext\tx"


def test_a_snippet_is_one_chunk_and_empty_text_none():
    assert chunk("A short search snippet.", "T", OPTS) == [Draft(0, 23)]
    assert chunk("   ", "T", OPTS) == []


def test_markdown_sections_heading_paths_and_fences():
    md = (
        "# Guide\n\nIntro.\n\n## Setup\n\nText.\n\n```\n# not a heading\n```\n\n"
        "=== Wiki ===\n\nMore."
    )
    assert is_markdown(md) and not is_markdown("plain text only")
    found = sections(md)
    assert [d.heading for d in found] == ["Guide", "Guide > Setup", "Guide > Setup > Wiki"]
    assert sections("Preface.\n\n# A\n\nx")[0] == Draft(0, 10, None)
    merged = merge_small([Draft(0, 10, "A"), Draft(10, 500, "B"), Draft(500, 510, "C")], 100)
    assert merged == [Draft(0, 500, "B"), Draft(500, 510, "C")]


def test_large_markdown_sections_split_with_their_heading():
    md = "# Guide\n\n## Setup\n\n" + "Install the package first. " * 60 + "\n\n## Use\n\nShort."
    drafts = chunk(md, "Doc", OPTS)
    assert len(drafts) > 2 and all(d.end - d.start <= 400 for d in drafts)
    assert {d.heading for d in drafts} == {"Guide > Setup", "Guide > Use"}


def test_prose_windows_overlap_and_cut_long_units():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 7 for i in range(20))
    spans = windows(text, 0, len(text), 400, 0.15)
    assert len(spans) > 1 and all(e - s <= 400 for s, e in spans)
    assert spans[1][0] < spans[0][1]  # the next window repeats the last paragraph
    long_sentence = "x" * 900
    assert windows(long_sentence, 0, 900, 400, 0.0) == [(0, 400), (400, 800), (800, 900)]
    words = "word " * 200
    cut = windows(words, 0, len(words), 120, 0.0)
    assert all(e - s <= 120 for s, e in cut) and words[cut[0][1] - 1] != " "


def test_pdf_chunks_never_cross_pages_and_short_pages_join():
    text = "Page one text. " * 30 + "\n\nTiny\n\n" + "Page three text. " * 30 + "\n\n" + "   "
    p2 = text.index("Tiny")
    p3 = text.index("Page three")
    pages = ((1, 0), (2, p2), (3, p3), (4, len(text) - 3))
    drafts = chunk(text, "Report", OPTS, pages)
    assert [d.page for d in drafts][0] == 1 and 2 in [d.page for d in drafts]
    assert all(not (d.start < p2 < d.end) for d in drafts if d.page == 1)
    assert drafts[-1].page in (2, 3)  # the blank last page is skipped


def test_embed_text_has_the_header():
    c = EvidenceChunk(
        "i", "demo", SourceKind.DOCUMENTS, "doc:1", "Guide", None, "1", "g.md", None,
        "Setup", 0, "Body", "h", "t",
    )  # fmt: skip
    assert embed_text(c) == "Guide · Setup\nBody"
