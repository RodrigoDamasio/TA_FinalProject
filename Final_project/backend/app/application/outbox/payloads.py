"""Message bodies, rendered at SEND time from the stored run (v1.1 §24, WEBHOOKS_PLAN §3.4–3.6).
A terminal run never changes, so every retry renders the same bytes. Telegram texts are plain
text (no parse mode: report text can't inject markup) and fit 4,096 UTF-16 code units.
Owner: WEBHOOKS."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from app.domain.outbox import (
    EVENT_ACCEPTED,
    EVENT_BOT_REPLY,
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    OutboxMessage,
)
from app.domain.report import Reference
from app.domain.run import ResearchRun, RunStatus

TELEGRAM_LIMIT = 4096  # UTF-16 code units, as Telegram counts them
TITLE_MAX = 200
ERROR_MAX = 300
MAX_REFERENCES = 5
FINISHED_EARLY = "[Finished early: the agents hit their limit.]"
_SENTENCE_END = re.compile(r"[.!?…](?=\s|$)")


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _cut_utf16(text: str, units: int) -> str:
    """The longest prefix of `text` that fits in `units` UTF-16 code units (never splits a
    surrogate pair)."""
    used = 0
    for i, ch in enumerate(text):
        used += 2 if ord(ch) > 0xFFFF else 1
        if used > units:
            return text[:i]
    return text


def _clip(text: str, chars: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[: chars - 1].rstrip() + "…"


def reference_line(ref: Reference) -> str:
    """`[E1] Title — domain` or `[E2] Title — handbook.pdf, p. 4`."""
    where = ""
    if ref.document_name:
        where = ref.document_name + (f", p. {ref.page}" if ref.page else "")
    elif ref.url:
        where = (urlsplit(ref.url).hostname or "").removeprefix("www.")
    title = _clip(ref.title, 120) or ref.id
    return f"[{ref.id}] {title}" + (f" — {where}" if where else "")


def telegram_report_text(
    title: str,
    summary: str,
    references: list[str],
    link: str,
    *,
    finished_early: bool = False,
    limit: int = TELEGRAM_LIMIT,
) -> str:
    """Deterministic fit (§3.4): drop references from the end, then cut the summary at the
    last sentence end that fits (+ `…`). The title (≤ 200) and the link are always kept."""
    title = _clip(title, TITLE_MAX)
    tail = (f"{FINISHED_EARLY}\n" if finished_early else "") + f"Full report: {link}"
    refs = list(references)

    def compose(body: str, refs: list[str]) -> str:
        blocks = [title, body.strip()]
        if refs:
            blocks.append("Sources:\n" + "\n".join(refs))
        return "\n\n".join(b for b in blocks if b) + "\n\n" + tail

    text = compose(summary, refs)
    while utf16_len(text) > limit and refs:
        refs.pop()
        text = compose(summary, refs)
    if utf16_len(text) <= limit:
        return text
    room = limit - utf16_len(compose("", [])) - len("\n\n…")
    prefix = _cut_utf16(summary.strip(), max(room, 0))
    ends = [m.end() for m in _SENTENCE_END.finditer(prefix)]
    cut = prefix[: ends[-1]] if ends else prefix
    return compose(cut.rstrip() + "…", [])


@dataclass(frozen=True)
class TelegramTexts:
    """Every text the bot sends (WEBHOOKS_PLAN §3.4)."""

    frontend_url: str
    runs_per_day: int = 10

    def web(self, run_id: str) -> str:
        return f"{self.frontend_url.rstrip('/')}/?run={run_id}"

    # ---- replies to a message (bot.reply rows, rendered by TelegramService) ----------------

    def start(self) -> str:
        return (
            "Hi! I'm a research assistant. Send me a question and a team of AI agents will "
            "search the web, Wikipedia and a document library, then send you a short cited "
            "report.\n\n"
            "Example: What are the trade-offs between RAG and fine-tuning?\n\n"
            "Your questions are processed by Google Gemini. Send /help for details."
        )

    def help(self) -> str:
        return (
            "Send a question (10–2,000 characters). I reply with a link to watch the agents "
            "live, then the report summary.\n"
            "Reports here are brief. For detailed or technical reports use the web app: "
            f"{self.frontend_url}\n"
            f"Limit: {self.runs_per_day} questions per day, one at a time.\n\n"
            "Commands: /start /help"
        )

    def not_allowed(self, chat_id: str) -> str:
        return f"This bot is private. Your chat id is {chat_id}. Ask the owner to add it."

    def text_only(self) -> str:
        return "I can only read text. Please send your question as a text message."

    def busy(self) -> str:
        return "I'm still working on your previous question. I'll send its report when it's ready."

    def rate_limited(self, retry_after_s: float) -> str:
        if retry_after_s <= 120:  # the per-minute limit (2 questions), not the daily one
            return "You're sending questions too fast. Please wait a minute and try again."
        hours = max(1, round(retry_after_s / 3600))
        return (
            f"You've reached the limit of {self.runs_per_day} questions per day. "
            f"Try again in about {hours} h."
        )

    def too_short(self) -> str:
        return "Please send a longer question (at least 10 characters)."

    def too_long(self, n: int) -> str:
        return f"That question is too long ({n:,} characters). The limit is 2,000."

    def busy_server(self) -> str:
        return "The server is busy. Please try again in a few minutes."

    def quota(self) -> str:
        return "Today's AI quota is used up. Please try again after midnight Pacific time."

    def unavailable(self) -> str:
        return "The AI service is unavailable right now. Please try again later."

    def error(self) -> str:
        return "Something went wrong on our side. Please try again later."

    # ---- run events (rendered by the dispatcher from the stored run) ---------------------

    def accepted(self, run: ResearchRun) -> str:
        return f"Working on it. Watch the agents live: {self.web(run.id)}"

    def completed(self, run: ResearchRun) -> str:
        early = run.forced or run.degraded
        if run.report is None:  # completed without a parsed report: the final Markdown
            body = (run.final_output or "").strip() or "The research is complete."
            return telegram_report_text(
                run.question, body, [], self.web(run.id), finished_early=early
            )
        refs = [reference_line(r) for r in run.report.references[:MAX_REFERENCES]]
        return telegram_report_text(
            run.report.title or run.question,
            run.report.summary,
            refs,
            self.web(run.id),
            finished_early=early,
        )

    def failed(self, run: ResearchRun) -> str:
        reason = _clip(run.error or "the run did not finish", ERROR_MAX)
        return f"Sorry, the research failed: {reason}\nYou can send the question again."

    def cancelled(self) -> str:
        return "The research was cancelled."

    def for_message(self, msg: OutboxMessage, run: ResearchRun | None) -> str:
        """The text of one Telegram outbox row. `bot.reply` rows carry their final text."""
        if msg.event == EVENT_BOT_REPLY or run is None:
            return _cut_utf16(str(msg.payload.get("text", "")), TELEGRAM_LIMIT)
        if msg.event == EVENT_ACCEPTED:
            return self.accepted(run)
        if msg.event == EVENT_COMPLETED:
            return self.completed(run)
        if msg.event == EVENT_FAILED:
            return self.failed(run)
        if msg.event == EVENT_CANCELLED:
            return self.cancelled()
        raise ValueError(f"unknown outbox event {msg.event!r}")


def utc_z(ts_iso: str) -> str:
    return datetime.fromisoformat(ts_iso).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def callback_body(
    msg: OutboxMessage, run: ResearchRun, view: dict[str, Any], base_url: str, web_url: str
) -> bytes:
    """§13.3 shape. `timestamp` is the run's `finished_at`, so retries send the same bytes;
    `conversation_history` is left out (size): receivers GET `links.self` for it."""
    data: dict[str, Any] = {
        "run_id": run.id,
        "status": run.status.value,
        "links": {"self": f"{base_url.rstrip('/')}/research/{run.id}", "web": web_url},
        "report": view.get("report") if run.status is RunStatus.COMPLETED else None,
        "metadata": view.get("metadata"),
    }
    if run.status is RunStatus.FAILED:
        data["error"] = run.error
    payload = {
        "type": msg.event,
        "timestamp": utc_z(run.finished_at or msg.created_at),
        "data": data,
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def unix(ts: datetime) -> int:
    return int(ts.timestamp())
