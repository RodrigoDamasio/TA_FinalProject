"""The outbox dispatcher: delivers due messages, retries on the RETRY_SCHEDULE_S, then marks
them dead. At-least-once: the webhook-id lets receivers drop duplicates. Step 3 version:
WEBHOOKS_PLAN adds claiming, Telegram retry_after, superseding and recovery.
Owner: WEBHOOKS."""

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.domain.errors import RunNotFound
from app.domain.outbox import OutboxMessage, next_attempt
from app.domain.ports import (
    OutboxRepository,
    RunRepository,
    SendResult,
    TelegramGateway,
    WebhookSender,
)
from app.domain.run import ResearchRun

from .payloads import callback_body, telegram_text, unix
from .signing import callback_secret, sign

logger = logging.getLogger("app.outbox")


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
    ) -> None:
        self._outbox, self._runs, self._telegram, self._sender = outbox, runs, telegram, sender
        self._view, self._key = view, server_key
        self._base, self._front = base_url.rstrip("/"), frontend_url.rstrip("/")
        self._max, self._clock = max_attempts, clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def web_url(self, run_id: str) -> str:
        return f"{self._front}/?run={run_id}"

    def _send(self, msg: OutboxMessage, now: datetime) -> SendResult:
        run: ResearchRun | None = None
        if msg.run_id:
            try:
                run = self._runs.get(msg.run_id)
            except RunNotFound:
                return SendResult(False, error="run no longer exists")
        if msg.kind == "telegram":
            chat = str(msg.payload.get("chat_id", msg.target))
            return self._telegram.send_message(
                chat, telegram_text(msg, run, self.web_url(msg.run_id))
            )
        assert run is not None  # noqa: S101 — callbacks always belong to a run
        body = callback_body(msg, run, self._view(run), self._base, self.web_url(run.id))
        ts = unix(now)
        secret = callback_secret(self._key, run.id)
        headers = {
            "content-type": "application/json",
            "user-agent": "TA-Research-Webhooks/1",
            "webhook-id": msg.id,
            "webhook-timestamp": str(ts),
            "webhook-signature": sign(secret, msg.id, ts, body),
        }
        return self._sender.post(msg.target, body, headers)

    def process_due(self, limit: int = 20) -> int:
        now = self._clock()
        handled = 0
        for msg in self._outbox.due(now, limit):
            handled += 1
            try:
                result = self._send(msg, now)
            except Exception as err:  # never kill the loop; record and retry
                logger.exception("outbox msg=%s send crashed", msg.id)
                result = SendResult(False, error=type(err).__name__)
            attempts = msg.attempts + 1
            if result.ok:
                self._outbox.mark_delivered(msg.id, now, result.status)
                continue
            error = (result.error or f"http {result.status}")[:200]
            later = next_attempt(attempts, now) if attempts < self._max else None
            if later is None:
                self._outbox.mark_dead(msg.id, attempts, error, result.status)
                logger.warning("outbox msg=%s kind=%s dead after %d", msg.id, msg.kind, attempts)
            else:
                self._outbox.mark_retry(msg.id, attempts, later, error, result.status)
        return handled

    # ---- background thread ------------------------------------------------------------

    def start(self, poll_s: float) -> None:
        def loop() -> None:
            while not self._stop.wait(poll_s):
                try:
                    self.process_due()
                except Exception:
                    logger.exception("outbox loop error")

        self._thread = threading.Thread(target=loop, name="outbox", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
