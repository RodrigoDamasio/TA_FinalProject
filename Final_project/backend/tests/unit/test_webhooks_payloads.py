"""Telegram texts, the 4,096-unit fit and the callback body (WEBHOOKS_PLAN §3.4–3.6, §9 unit)."""

import json

import pytest

from app.application.outbox.payloads import (
    FINISHED_EARLY,
    TelegramTexts,
    callback_body,
    reference_line,
    telegram_report_text,
    utf16_len,
)
from app.domain.depth import Depth
from app.domain.evidence import SourceKind
from app.domain.outbox import OutboxMessage
from app.domain.report import Reference, Report, ReportSection
from app.domain.run import ResearchRun

TEXTS = TelegramTexts("https://ui.example", runs_per_day=10)
LINK = "https://ui.example/?run=run_1"


def ref(n: int, **kw) -> Reference:
    base = {
        "id": f"E{n}",
        "title": f"Source {n}",
        "url": f"https://www.site{n}.org/page",
        "document_name": None,
        "page": None,
        "source_kind": SourceKind.WEB,
        "snippet": "",
    }
    return Reference(**(base | kw))


def report(summary="RAG retrieves at query time [E1].", refs=6) -> Report:
    return Report(
        "RAG vs fine-tuning",
        summary,
        (ReportSection("Overview", "Text [E1]."),),
        tuple(ref(i) for i in range(1, refs + 1)),
        Depth.BRIEF,
        120,
    )


def finished(rep: Report | None = None, forced=False, final="# T\n\nSummary.") -> ResearchRun:
    run = ResearchRun.create(
        "What are the trade-offs between RAG and fine-tuning?", 4, False, False
    )
    run.start()
    if rep is not None:
        run.set_report(rep)
    run.propose_final(final, forced=forced, degraded=False)
    return run


def message(run: ResearchRun, event: str) -> OutboxMessage:
    return OutboxMessage.new(run.id, "telegram", event, "42", {"chat_id": "42"})


def test_reference_lines():
    assert reference_line(ref(1)) == "[E1] Source 1 — site1.org"
    doc = ref(2, url=None, document_name="handbook.pdf", page=4)
    assert reference_line(doc) == "[E2] Source 2 — handbook.pdf, p. 4"
    assert reference_line(ref(3, url=None, document_name="notes.md")) == "[E3] Source 3 — notes.md"
    assert reference_line(ref(4, url=None, title="")) == "[E4] E4"


def test_completed_text_layout():
    run = finished(report())
    text = TEXTS.for_message(message(run, "research.completed"), run)
    lines = text.split("\n")
    assert lines[:5] == [
        "RAG vs fine-tuning",
        "",
        "RAG retrieves at query time [E1].",
        "",
        "Sources:",
    ]
    assert lines[5:10] == [f"[E{i}] Source {i} — site{i}.org" for i in range(1, 6)]  # ≤ 5
    assert lines[10:] == ["", f"Full report: https://ui.example/?run={run.id}"]
    assert FINISHED_EARLY not in text


def test_completed_flags_forced_and_handles_a_missing_report():
    forced = finished(report(refs=0), forced=True)
    text = TEXTS.completed(forced)
    assert "Sources:" not in text and f"{FINISHED_EARLY}\nFull report:" in text
    bare = finished(None)
    text = TEXTS.completed(bare)
    assert text.startswith(bare.question + "\n\n# T\n\nSummary.")
    assert text.endswith(f"Full report: https://ui.example/?run={bare.id}")


def test_other_run_events():
    run = finished(report())
    assert TEXTS.for_message(message(run, "research.accepted"), run) == (
        f"Working on it. Watch the agents live: https://ui.example/?run={run.id}"
    )
    failed = ResearchRun.create("Another question here", 4, False, False)
    failed.start()
    failed.fail("x" * 400)
    text = TEXTS.for_message(message(failed, "research.failed"), failed)
    assert text.startswith("Sorry, the research failed: xxx") and text.endswith(
        "…\nYou can send the question again."
    )
    assert len(text.split("\n")[0]) <= len("Sorry, the research failed: ") + 300
    failed.error = None
    assert "the run did not finish" in TEXTS.failed(failed)
    assert TEXTS.for_message(message(run, "research.cancelled"), run) == (
        "The research was cancelled."
    )
    reply = OutboxMessage.new("", "telegram", "bot.reply", "7", {"chat_id": "7", "text": "Hi"})
    assert TEXTS.for_message(reply, None) == "Hi"
    with pytest.raises(ValueError, match="unknown outbox event"):
        TEXTS.for_message(message(run, "research.unknown"), run)


def test_reply_texts():
    assert TEXTS.start().startswith("Hi! I'm a research assistant.")
    assert "Google Gemini" in TEXTS.start()
    assert "web app: https://ui.example" in TEXTS.help() and "Limit: 10 questions" in TEXTS.help()
    assert "Your chat id is 99." in TEXTS.not_allowed("99")
    assert TEXTS.rate_limited(5 * 3600 + 100) == (
        "You've reached the limit of 10 questions per day. Try again in about 5 h."
    )
    assert "about 1 h" in TEXTS.rate_limited(600)
    assert TEXTS.rate_limited(30).startswith("You're sending questions too fast.")
    assert "(2,345 characters)" in TEXTS.too_long(2345)
    for text in (TEXTS.text_only(), TEXTS.busy(), TEXTS.too_short(), TEXTS.busy_server(),
                 TEXTS.quota(), TEXTS.unavailable(), TEXTS.error()):  # fmt: skip
        assert text and "\n" not in text


def test_fit_drops_references_from_the_end_first():
    refs = [f"[E{i}] " + "r" * 900 for i in range(1, 6)]
    text = telegram_report_text("Title", "Short summary.", refs, LINK)
    assert utf16_len(text) <= 4096
    assert "[E4]" in text and "[E5]" not in text and "Short summary." in text


def test_fit_cuts_the_summary_at_a_sentence_end():
    summary = " ".join(f"Sentence number {i} is here." for i in range(400))
    text = telegram_report_text("T" * 300, summary, ["[E1] a"], LINK, finished_early=True)
    assert utf16_len(text) <= 4096 and text.startswith("T" * 199 + "…\n\n")  # title ≤ 200
    assert "Sources:" not in text
    body = text.split("\n\n")[1]
    assert body.endswith("is here.…")
    assert text.endswith(f"{FINISHED_EARLY}\nFull report: {LINK}")


def test_fit_counts_utf16_units_and_never_splits_emoji():
    summary = "😀" * 3000  # 6,000 UTF-16 units, no sentence end
    text = telegram_report_text("Title", summary, [], LINK)
    assert utf16_len(text) <= 4096 and len(text) < 2100
    assert text.endswith(f"…\n\nFull report: {LINK}")
    assert text.encode("utf-8")  # no lone surrogate: an emoji is never split


def test_text_that_fits_is_unchanged():
    text = telegram_report_text("Title", "Summary.", ["[E1] a"], LINK)
    assert text == f"Title\n\nSummary.\n\nSources:\n[E1] a\n\nFull report: {LINK}"


def test_callback_body_shape_and_byte_stability():
    run = finished(report())
    msg = OutboxMessage.new(run.id, "callback", "research.completed", "https://h.example/x")
    view = {"report": {"title": "R"}, "metadata": {"llm_calls": 3}, "conversation_history": [1]}
    body = callback_body(msg, run, view, "https://api.example/", "https://ui/?run=" + run.id)
    assert body == callback_body(
        msg, run, view, "https://api.example/", "https://ui/?run=" + run.id
    )
    data = json.loads(body)
    assert data["type"] == "research.completed" and data["timestamp"].endswith("Z")
    assert data["data"] == {
        "run_id": run.id,
        "status": "completed",
        "links": {
            "self": f"https://api.example/research/{run.id}",
            "web": "https://ui/?run=" + run.id,
        },
        "report": {"title": "R"},
        "metadata": {"llm_calls": 3},
    }
    assert b", " not in body and b": " not in body  # compact separators

    failed = ResearchRun.create("Another question here", 4, False, False)
    failed.start()
    failed.fail("boom")
    data = json.loads(callback_body(msg, failed, view, "https://api.example", "w"))["data"]
    assert data["report"] is None and data["error"] == "boom" and data["status"] == "failed"
