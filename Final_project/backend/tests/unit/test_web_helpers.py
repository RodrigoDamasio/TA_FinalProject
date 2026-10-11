"""Argument and URL helpers of the web tools (SEARXNG_TOOL_PLAN §3.4, §9)."""

import pytest

from app.application.tools.common import (
    cut_at_paragraph,
    normalize_query,
    normalize_url,
    parse_evidence_id,
    wiki_headings_to_markdown,
)
from app.infrastructure.searxng import normalize_query as cache_key_query


@pytest.mark.parametrize(
    ("url", "key"),
    [
        ("HTTPS://Example.COM:443/a/?utm_source=x&id=3#top", "https://example.com/a?id=3"),
        ("http://example.com:80/", "http://example.com/"),
        ("http://example.com", "http://example.com/"),
        ("https://example.com:8443/p/", "https://example.com:8443/p"),
        ("https://a.org/p?gclid=1&fbclid=2", "https://a.org/p"),
        ("  https://a.org/p?b=2&a=1  ", "https://a.org/p?b=2&a=1"),  # order kept
    ],
)
def test_normalize_url(url, key):
    assert normalize_url(url) == key


def test_query_and_evidence_id_parsing():
    assert normalize_query("  ｒａｇ \t  tools\n") == "rag tools"  # NFKC + whitespace
    assert normalize_query("x") is None and normalize_query("y" * 301) is None
    assert normalize_query(42) is None and normalize_query(None) is None
    assert cache_key_query("  RAG   Tools ") == cache_key_query("rag tools")
    for value, n in (("E3", 3), ("[E12]", 12), ("e7", 7), (" 4 ", 4), (5, 5)):
        assert parse_evidence_id(value) == n
    for value in ("E", "E12345", "source 3", None, "[E3", "E-1"):
        assert parse_evidence_id(value) is None or value == "[E3"


def test_wiki_headings_and_paragraph_cut():
    text = "Intro.\n== History ==\nOld.\n=== Early years ===\nOlder.\n= Not a heading =\n"
    assert wiki_headings_to_markdown(text) == (
        "Intro.\n## History\nOld.\n### Early years\nOlder.\n= Not a heading =\n"
    )
    body = "a" * 60 + "\n\n" + "b" * 60
    assert cut_at_paragraph(body, 500) == (body, False)
    assert cut_at_paragraph(body, 100) == ("a" * 60, True)  # cut at the paragraph boundary
    assert cut_at_paragraph("c" * 300, 100) == ("c" * 100, True)  # no boundary: hard cut
