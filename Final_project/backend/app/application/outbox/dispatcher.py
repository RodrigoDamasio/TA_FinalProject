"""The outbox dispatcher (WEBHOOKS_PLAN §3.7): delivers due messages, retries on the schedule
(attempts at 0 s, 10 s, 1 min, 5 min, 30 min, 2 h), then marks them dead. At least once: the
`webhook-id` lets receivers drop duplicates.

One daemon thread in one replica handles rows sequentially; a lock makes each pass exclusive
(the thread, or a test calling `tick`), which is the whole claim: no lease column.
Owner: WEBHOOKS."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from app.domain.errors import RunNotFound
from app.domain.outbox import (
    EVENT_ACCEPTED,
    EVENT_CANCELLED,
    EVENT_COMPLETED,
    EVENT_FAILED,
    OutboxMessage,
    OutboxStatus,
    iso,
)
from app.domain.ports import (
    OutboxRepository,
    RunRepository,
    SendResult,
    TelegramGateway,
    UpdateLedger,
    WebhookSender,
)
from app.domain.run import ResearchRun

from .payloads import TelegramTexts, callback_body, unix
from .schedule import retry_at
from .signing import callback_secret, signed_headers

logger = logging.getLogger("app.outbox")

TERMINAL_EVENTS = frozenset({EVENT_COMPLETED, EVENT_FAILED, EVENT_CANCELLED})
OUTBOX_RETENTION_DAYS = 30
UPDATES_RETENTION_DAYS = 7  # Telegram stops redelivering after about a day
ERROR_MAX = 200

Outcome = Literal["delivered", "retry", "dead"]


def classify(kind: str, result: SendResult) -> Outcome:
    """The outcome table of WEBHOOKS_PLAN §3.7."""
    if result.ok:
        return "delivered"
    if (result.error or "").startswith("refused:"):  # SSRF guard: permanent
        return "dead"
    status = result.status
    if status is None or status == 429 or status >= 500:  # network, rate limit, server
        return "retry"
    if kind == "telegram":  # 400/403 (chat not found, bot blocked), other 4xx, 3xx
        return "dead"
    return "dead" if status == 410 else "retry"  # callbacks: only 410 Gone is permanent


def error_code(kind: str, result: SendResult) -> str:
    """Short, never a URL, a token or a response body."""
    if result.error:
        return result.error[:ERROR_MAX]
    return f"{'telegram' if kind == 'telegram' else 'http'} {result.status}"


class OutboxDispatcher:
    def __init__(
        self,
        outbox: OutboxRepository,
        runs: RunRepository,
        telegram: TelegramGateway,
        sender: WebhookSender,
        view: Callable[[ResearchRun], dict[str, Any]],
        server_key: bytes,
        *,
        base_url: str,
        frontend_url: str,
        max_attempts: int = 6,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        batch: int = 10,
        poll_s: float = 1.0,
        ledger: UpdateLedger | None = None,
    ) -> None:
        self._outbox, self._runs, self._telegram, self._sender = outbox, runs, telegram, sender
        self._view, self._key = view, server_key
        self._base = base_url.rstrip("/")
        self._texts = TelegramTexts(frontend_url.rstrip("/"))
        self._max, self._clock, self._batch, self._poll = max_attempts, clock, batch, poll_s
        self._ledger = ledger
        self._pass = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def web_url(self, run_id: str) -> str:
        return self._texts.web(run_id)

    # ---- one pass ------------------------------------------------------------------------

    def tick(self) -> int:
        """Deliver the due messages (≤ batch), in `next_attempt_at` then `created_at` order.
        Returns how many were handled."""
        with self._pass:
            now = self._clock()
            superseded: set[str] = set()
            handled = 0
            for msg in self._outbox.due(now, self._batch):
                if msg.id in superseded:
                    continue
                handled += 1
                self._deliver(msg, now, superseded)
            return handled

    def process_due(self) -> int:
        """Step 3 name of `tick` (kept for callers and tests)."""
        return self.tick()

    def _deliver(self, msg: OutboxMessage, now: datetime, superseded: set[str]) -> None:
        started = time.monotonic()
        run: ResearchRun | None = None
        if msg.run_id:
            try:
                run = self._runs.get(msg.run_id)
            except RunNotFound:  # removed by retention before delivery
                self._outbox.mark_dead(msg.id, msg.attempts, "run deleted")
                logger.warning("outbox msg=%s run=%s dead: run deleted", msg.id, msg.run_id)
                return
        if run is not None and msg.kind == "telegram" and msg.event in TERMINAL_EVENTS:
            superseded |= self._supersede_accepted(run.id)
        try:
            result = self._send(msg, run, now)
        except Exception as err:  # never kill the loop; record and retry
            logger.error("outbox msg=%s send crashed error=%s", msg.id, type(err).__name__)
            result = SendResult(False, error="error")
        attempts = msg.attempts + 1
        outcome = classify(msg.kind, result)
        later = None
        if outcome == "retry":
            retry_after = result.retry_after_s if result.status == 429 else None
            later = retry_at(attempts, now, self._max, retry_after)
            outcome = "retry" if later is not None else "dead"
        if outcome == "delivered":
            self._outbox.mark_delivered(msg.id, now, result.status)
        elif later is not None:
            self._outbox.mark_retry(
                msg.id, attempts, later, error_code(msg.kind, result), result.status
            )
        else:
            self._outbox.mark_dead(msg.id, attempts, error_code(msg.kind, result), result.status)
        logger.info(
            "outbox msg=%s run=%s kind=%s event=%s outcome=%s status=%s attempt=%d ms=%d",
            msg.id,
            msg.run_id or "-",
            msg.kind,
            msg.event,
            outcome,
            result.status,
            attempts,
            int((time.monotonic() - started) * 1000),
        )

    def _supersede_accepted(self, run_id: str) -> set[str]:
        """No "working on it" after the report: a still-pending `research.accepted` row of the
        same run goes dead before a terminal message is sent."""
        stale = [
            m
            for m in self._outbox.for_run(run_id)
            if m.event == EVENT_ACCEPTED and m.status is OutboxStatus.PENDING
        ]
        for m in stale:
            self._outbox.mark_dead(m.id, m.attempts, "superseded", m.last_status)
        return {m.id for m in stale}

    def _send(self, msg: OutboxMessage, run: ResearchRun | None, now: datetime) -> SendResult:
        if msg.kind == "telegram":
            chat = str(msg.payload.get("chat_id") or msg.target)
            return self._telegram.send_message(chat, self._texts.for_message(msg, run))
        if run is None:  # a callback always belongs to a run
            return SendResult(False, error="run deleted")
        body = callback_body(msg, run, self._view(run), self._base, self.web_url(run.id))
        secret = callback_secret(self._key, run.id)  # derived again, never stored
        return self._sender.post(msg.target, body, signed_headers(secret, msg.id, unix(now), body))

    # ---- housekeeping --------------------------------------------------------------------

    def sweep(self, now: datetime | None = None) -> dict[str, int]:
        """Delete delivered and dead rows after 30 days, and received update ids after 7."""
        now = now or self._clock()
        rows = self._outbox.delete_older_than(iso(now - timedelta(days=OUTBOX_RETENTION_DAYS)))
        updates = 0
        if self._ledger is not None:
            updates = self._ledger.forget_older_than(
                iso(now - timedelta(days=UPDATES_RETENTION_DAYS))
            )
        if rows or updates:
            logger.info("outbox sweep rows=%d updates=%d", rows, updates)
        return {"outbox": rows, "updates": updates}

    # ---- background thread ------------------------------------------------------------

    def start(self, poll_s: float | None = None) -> None:
        """Daemon thread "outbox". Pending rows survive restarts: overdue ones go first."""
        if self._thread is not None and self._thread.is_alive():
            return
        poll = self._poll if poll_s is None else poll_s
        self._stop.clear()

        def loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as err:  # a pass may fail (database busy); the loop never dies
                    logger.error("outbox loop error=%s", type(err).__name__)
                self._stop.wait(poll)

        self._thread = threading.Thread(target=loop, name="outbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
