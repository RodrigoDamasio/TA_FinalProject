"""Webhooks and the outbox (step 3 versions): signing, rendering at send time, retries,
dead letters and the SQLite stores."""

import base64
import json
from datetime import UTC, datetime, timedelta

from fakes import FakeClock, FakeTelegram, FakeWebhookSender

from app.application.outbox.dispatcher import OutboxDispatcher
from app.application.outbox.payloads import telegram_text
from app.application.outbox.signing import callback_secret, sign, verify
from app.domain.outbox import (
    EVENT_ACCEPTED,
    EVENT_BOT_REPLY,
    OutboxMessage,
    OutboxStatus,
    Subscriber,
)
from app.domain.ports import SendResult
from app.domain.run import ResearchRun
from app.infrastructure.sqlite_store import (
    SqliteOutboxRepository,
    SqlitePageCache,
    SqliteRunRepository,
    SqliteSearchCache,
    SqliteUpdateLedger,
)

KEY = b"server-secret"


def test_signing_follows_standard_webhooks():
    secret = callback_secret(KEY, "run_1")
    assert secret.startswith("whsec_") and secret == callback_secret(KEY, "run_1")
    assert secret != callback_secret(KEY, "run_2")
    vector = "whsec_" + base64.b64encode(b"k" * 32).decode()
    sig = sign(vector, "msg_1", 1700000000, b'{"a":1}')
    assert sig.startswith("v1,") and verify(vector, "msg_1", 1700000000, b'{"a":1}', sig)
    assert not verify(vector, "msg_1", 1700000001, b'{"a":1}', sig)


def completed_run(repo, **kw) -> ResearchRun:
    run = ResearchRun.create("A research question", 5, False, False, **kw)
    run.start()
    run.propose_final("# T\n\nSummary.", forced=False, degraded=False)
    repo.save(run)  # state + outbox rows in one transaction
    return run


def dispatcher(tmp_path, clock, sender=None, telegram=None, max_attempts=6):
    db = str(tmp_path / "o.db")
    runs, outbox = SqliteRunRepository(db), SqliteOutboxRepository(db)
    d = OutboxDispatcher(
        outbox, runs, telegram or FakeTelegram(), sender or FakeWebhookSender(),
        lambda run: {"report": None, "metadata": {"llm_calls": 0}}, KEY,
        base_url="https://api.example", frontend_url="https://ui.example",
        max_attempts=max_attempts, clock=clock,
    )  # fmt: skip
    return d, runs, outbox


def test_callback_is_signed_at_send_time_and_verifiable(tmp_path):
    clock = FakeClock(datetime.now(UTC) + timedelta(seconds=2))  # messages created now are due
    sender = FakeWebhookSender()
    d, runs, outbox = dispatcher(tmp_path, clock, sender)
    run = completed_run(runs, subscribers=[Subscriber("callback", "https://r.example/hook")])
    assert [m.event for m in outbox.for_run(run.id)] == ["research.completed"]
    assert d.process_due() == 1
    url, body, headers = sender.posts[0]
    secret = callback_secret(KEY, run.id)
    assert url == "https://r.example/hook" and verify(
        secret, headers["webhook-id"], int(headers["webhook-timestamp"]), body,
        headers["webhook-signature"],
    )  # fmt: skip
    payload = json.loads(body)
    assert payload["type"] == "research.completed" and payload["data"]["run_id"] == run.id
    assert payload["data"]["links"]["web"] == f"https://ui.example/?run={run.id}"
    (msg,) = outbox.for_run(run.id)
    assert msg.status is OutboxStatus.DELIVERED and msg.last_status == 200


def test_failed_deliveries_retry_then_go_dead(tmp_path):
    clock = FakeClock(datetime.now(UTC) + timedelta(seconds=2))  # messages created now are due
    sender = FakeWebhookSender(SendResult(False, 500))
    d, runs, outbox = dispatcher(tmp_path, clock, sender, max_attempts=2)
    run = completed_run(runs, subscribers=[Subscriber("callback", "https://r.example/hook")])
    d.process_due()
    (msg,) = outbox.for_run(run.id)
    assert msg.status is OutboxStatus.PENDING and msg.attempts == 1 and msg.last_error == "http 500"
    assert d.process_due() == 0  # not due yet (10 s later)
    clock.advance(11)
    d.process_due()
    (msg,) = outbox.for_run(run.id)
    assert msg.status is OutboxStatus.DEAD and msg.attempts == 2
    assert outbox.counts()["dead"] == 1


def test_telegram_messages_and_texts(tmp_path):
    clock = FakeClock(datetime.now(UTC) + timedelta(seconds=2))  # messages created now are due
    telegram = FakeTelegram()
    d, runs, outbox = dispatcher(tmp_path, clock, telegram=telegram)
    run = completed_run(runs, subscribers=[Subscriber("telegram", "42")])
    outbox.add(
        OutboxMessage.new("", "telegram", EVENT_BOT_REPLY, "7", {"chat_id": "7", "text": "Hi"})
    )
    d.process_due()
    assert ("7", "Hi") in telegram.sent
    chat, text = next(x for x in telegram.sent if x[0] == "42")
    assert text.startswith("# T\n\nSummary.") and f"?run={run.id}" in text  # no parsed report
    accepted = OutboxMessage.new(run.id, "telegram", EVENT_ACCEPTED, "42", {"chat_id": "42"})
    assert telegram_text(accepted, run, "https://ui/x").startswith("Working on it.")
    failed = ResearchRun.create("Another question here", 5, False, False)
    failed.start()
    failed.fail("boom")
    done = OutboxMessage.new(failed.id, "telegram", "research.failed", "42")
    assert "could not be completed (boom)" in telegram_text(done, failed, "u")


def test_a_message_whose_run_was_deleted_is_not_delivered(tmp_path):
    clock = FakeClock(datetime.now(UTC) + timedelta(seconds=2))  # messages created now are due
    d, runs, outbox = dispatcher(tmp_path, clock)
    outbox.add(OutboxMessage.new("run_gone", "callback", "research.completed", "https://x.org/h"))
    d.process_due()
    (msg,) = outbox.for_run("run_gone")
    assert msg.last_error == "run no longer exists"


def test_ledger_caches_and_retention(tmp_path):
    db = str(tmp_path / "s.db")
    ledger = SqliteUpdateLedger(db)
    assert ledger.seen(1) is False and ledger.seen(1) is True
    assert ledger.forget_older_than("2999-01-01") == 1
    now = {"t": 1000.0}
    search = SqliteSearchCache(db, clock=lambda: now["t"])
    search.put("k", [{"title": "a"}])
    assert search.get("k", 60) == [{"title": "a"}] and search.get("missing", 60) is None
    now["t"] += 120
    assert search.get("k", 60) is None
    pages = SqlitePageCache(db, clock=lambda: now["t"])
    pages.put("h", "https://a.org", "text", "text/html")
    assert pages.get("h", 60) == ("https://a.org", "text", "text/html")
    assert pages.get("x", 60) is None
    outbox = SqliteOutboxRepository(db)
    outbox.add(OutboxMessage.new("r", "callback", "e", "https://a.org"))
    assert outbox.counts()["pending"] == 1
    assert outbox.delete_older_than("2999-01-01") == 0  # pending rows are kept
