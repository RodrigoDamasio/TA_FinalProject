"""Telegram inbound updates → RunService (WEBHOOKS_PLAN §3.1–3.2). Synchronous, no LLM call,
a few SQLite writes. Every reply is a `bot.reply` outbox row (delivered with retries by the
dispatcher), so the webhook answers Telegram at once. `handle` never raises.

Logs: update id, a chat fingerprint, run id and the decision. Never the text.
Owner: WEBHOOKS."""

import hashlib
import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable

from pydantic import BaseModel, ConfigDict

from app.domain.depth import Depth
from app.domain.errors import (
    InputTooLarge,
    InvalidField,
    LLMQuotaExceeded,
    LLMUnavailable,
    QueueFull,
)
from app.domain.evidence import REQUESTABLE_SOURCES, SourceKind
from app.domain.outbox import EVENT_BOT_REPLY, Channel, OutboxMessage
from app.domain.ports import OutboxRepository
from app.domain.run import RunStatus

from .outbox.payloads import TelegramTexts
from .runs import RunService, SubmitCommand

logger = logging.getLogger("app.telegram")

NOT_ALLOWED_EVERY_S = 3600.0  # at most one reply per unknown chat per hour
RUNS_PER_MINUTE = 2


# ---- the update, parsed leniently (only what the bot uses) ---------------------------------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TelegramChat(_Lenient):
    id: int
    type: str = "private"


class TelegramMessage(_Lenient):
    message_id: int
    chat: TelegramChat
    text: str | None = None


class TelegramUpdate(_Lenient):
    update_id: int
    message: TelegramMessage | None = None  # edited messages, channel posts… are ignored


def chat_fingerprint(chat_id: str) -> str:
    return hashlib.sha256(chat_id.encode()).hexdigest()[:8]


def command_of(text: str) -> str | None:
    """`/start`, `/START@my_bot args` → "start"; None for ordinary text."""
    if not text.startswith("/"):
        return None
    return text.split(maxsplit=1)[0][1:].split("@", 1)[0].lower()


class ChatLimiter:
    """Per-chat run limits, in memory (WEBHOOKS Q6). Unlike the M5 IP limiter, checking and
    recording are separate: only an ACCEPTED run uses the day's allowance."""

    WINDOWS = ((60.0, "minute"), (86400.0, "day"))

    def __init__(
        self, per_minute: int, per_day: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limits = {"minute": per_minute, "day": per_day}
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, key: str) -> float | None:
        """Seconds until `key` may run again; None when it may run now."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= 86400:
                hits.popleft()
            waits = []
            for seconds, window in self.WINDOWS:
                recent = [t for t in hits if now - t < seconds]
                if len(recent) >= self._limits[window]:
                    waits.append(seconds - (now - recent[-self._limits[window]]))
            return max(waits) if waits else None

    def record(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(self._clock())


class TelegramService:
    def __init__(
        self,
        runs: RunService | None = None,
        outbox: OutboxRepository | None = None,
        *,
        allowed_chats: Iterable[str] = (),
        texts: TelegramTexts | None = None,
        limiter: ChatLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
        sources: frozenset[SourceKind] = REQUESTABLE_SOURCES,
    ) -> None:
        self._runs, self._outbox = runs, outbox
        self._allowed = frozenset(str(c).strip() for c in allowed_chats if str(c).strip())
        self.texts = texts or TelegramTexts("http://localhost:3000")
        self._limiter = limiter or ChatLimiter(RUNS_PER_MINUTE, self.texts.runs_per_day, clock)
        self._clock, self._sources = clock, sources
        self._told: dict[str, float] = {}  # chat → last NOT_ALLOWED reply (monotonic)
        self._lock = threading.Lock()

    @property
    def wired(self) -> bool:
        """False for the placeholder the step-3 container builds (`TelegramService()`)."""
        return self._runs is not None and self._outbox is not None

    def handle(self, update: TelegramUpdate) -> str:
        """Decide and act on one update. Returns the decision (for logs and tests)."""
        msg = update.message
        if msg is None:
            return self._log(update, None, "ignored")
        chat = str(msg.chat.id)
        try:
            decision = self._decide(chat, (msg.text or "").strip(), msg.text is None)
        except Exception as err:  # never raise to the route: Telegram would resend
            logger.error("telegram update=%d failed error=%s", update.update_id, type(err).__name__)
            decision = "error"
            self._reply_quietly(chat, self.texts.error())
        return self._log(update, chat, decision)

    # ---- the decision table (§3.2, first match wins) ---------------------------------------

    def _decide(self, chat: str, text: str, no_text: bool) -> str:
        if chat not in self._allowed:
            if not self._should_tell(chat):
                return "not_allowed_quiet"
            self._reply(chat, self.texts.not_allowed(chat))
            return "not_allowed"
        if no_text:
            self._reply(chat, self.texts.text_only())
            return "text_only"
        command = command_of(text)
        if command == "start":
            self._reply(chat, self.texts.start())
            return "start"
        if command is not None:
            self._reply(chat, self.texts.help())
            return "help"
        if self._has_active_run(chat):
            self._reply(chat, self.texts.busy())
            return "busy"
        key = f"tg:{chat}"
        wait = self._limiter.retry_after(key)
        if wait is not None:
            self._reply(chat, self.texts.rate_limited(wait))
            return "rate_limited"
        return self._submit(chat, text, key)

    def _submit(self, chat: str, text: str, key: str) -> str:
        assert self._runs is not None  # noqa: S101 — checked by `wired` at wiring time
        cmd = SubmitCommand(
            question=text,
            depth=Depth.BRIEF,  # quota: Telegram reports are always brief (Q5)
            sources=self._sources,  # documents = the demo library (no workspace)
            max_iterations=None,
            require_approval=False,
            use_memory=True,
            workspace_id=None,
            channel=Channel.TELEGRAM,
            callback_url=None,
            telegram_chat_id=chat,
            client_key=key,
        )
        try:
            run = self._runs.submit(cmd)  # queues research.accepted with the run
        except InvalidField:
            self._reply(chat, self.texts.too_short())
            return "too_short"
        except InputTooLarge:
            self._reply(chat, self.texts.too_long(len(text)))
            return "too_long"
        except QueueFull:
            self._reply(chat, self.texts.busy_server())
            return "busy_server"
        except LLMQuotaExceeded:
            self._reply(chat, self.texts.quota())
            return "quota"
        except LLMUnavailable:
            self._reply(chat, self.texts.unavailable())
            return "unavailable"
        self._limiter.record(key)  # only accepted runs use the allowance
        logger.info("telegram chat=%s run=%s submitted", chat_fingerprint(chat), run.id)
        return "submitted"

    # ---- helpers ---------------------------------------------------------------------------

    def _should_tell(self, chat: str) -> bool:
        now = self._clock()
        with self._lock:
            last = self._told.get(chat)
            if last is not None and now - last < NOT_ALLOWED_EVERY_S:
                return False
            self._told[chat] = now
            if len(self._told) > 10_000:  # bound the memory a flood of chats can take
                self._told = {c: t for c, t in self._told.items() if now - t < 3600}
            return True

    def _has_active_run(self, chat: str) -> bool:
        assert self._runs is not None  # noqa: S101
        active = self._runs.runs.runs_in({RunStatus.QUEUED, RunStatus.RUNNING})
        return any(
            s.kind == "telegram" and s.target == chat for run in active for s in run.subscribers
        )

    def _reply(self, chat: str, text: str) -> None:
        assert self._outbox is not None  # noqa: S101
        self._outbox.add(
            OutboxMessage.new(
                "", "telegram", EVENT_BOT_REPLY, chat, {"chat_id": chat, "text": text}
            )
        )

    def _reply_quietly(self, chat: str, text: str) -> None:
        try:
            self._reply(chat, text)
        except Exception as err:
            logger.error("telegram reply failed error=%s", type(err).__name__)

    def _log(self, update: TelegramUpdate, chat: str | None, decision: str) -> str:
        logger.info(
            "telegram update=%d chat=%s decision=%s",
            update.update_id,
            chat_fingerprint(chat) if chat else "-",
            decision,
        )
        return decision
