"""The outbox dispatcher with a temp SQLite outbox, fake senders and a fake clock
(WEBHOOKS_PLAN §3.7, §9 component): delivery, retries, dead letters, 429 timing, superseded
"working on it" rows, deleted runs, bot replies, the sweeper and the background thread."""

import json
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeClock, FakeTelegram, FakeWebhookSender

from app.application.outbox.dispatcher import OutboxDispatcher, classify, error_code
from app.application.outbox.signing import callback_secret, verify
from app.domain.outbox import (
    EVENT_ACCEPTED,
    EVENT_BOT_REPLY,
    OutboxMessage,
    OutboxStatus,
    Subscriber,
    iso,
)
from app.domain.ports import SendResult
from app.domain.run import ResearchRun
from app.infrastructure.sqlite_store import (
    SqliteOutboxRepository,
    SqliteRunRepository,
    SqliteUpdateLedger,
)

KEY = b"server-secret"
HOOK = "https://r.example/hook"


def soon() -> FakeClock:
    return FakeClock(datetime.now(UTC) + timedelta(seconds=2))  # rows created now are due


def completed_run(repo, **kw) -> ResearchRun:
    run = ResearchRun.create("A research question", 5, False, False, **kw)
    run.start()
    run.propose_final("# T\n\nSummary.", forced=False, degraded=False)
    repo.save(run)  # state + outbox rows in one transaction
    return run


def dispatcher(tmp_path, clock, sender=None, telegram=None, max_attempts=6, ledger=None):
    db = str(tmp_path / "o.db")
    runs, outbox = SqliteRunRepository(db), SqliteOutboxRepository(db)
    d = OutboxDispatcher(
        outbox, runs, telegram or FakeTelegram(), sender or FakeWebhookSender(),
        lambda run: {"report": None, "metadata": {"llm_calls": 0}}, KEY,
        base_url="https://api.example", frontend_url="https://ui.example/",
        max_attempts=max_attempts, clock=clock, ledger=ledger,
    )  # fmt: skip
    return d, runs, outbox


def only(outbox, run_id) -> OutboxMessage:
    (msg,) = outbox.for_run(run_id)
    return msg


# ---- outcome table (unit) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "result", "outcome"),
    [
        ("telegram", SendResult(True, 200), "delivered"),
        ("callback", SendResult(True, 204), "delivered"),
        ("telegram", SendResult(False, 429, 5.0), "retry"),
        ("callback", SendResult(False, 429), "retry"),
        ("telegram", SendResult(False, 502), "retry"),
        ("callback", SendResult(False, 503), "retry"),
        ("telegram", SendResult(False, error="timeout"), "retry"),
        ("callback", SendResult(False, error="connect"), "retry"),
        ("telegram", SendResult(False, 400), "dead"),
        ("telegram", SendResult(False, 403), "dead"),
        ("telegram", SendResult(False, 404), "dead"),
        ("telegram", SendResult(False, 302), "dead"),
        ("callback", SendResult(False, 400), "retry"),
        ("callback", SendResult(False, 403), "retry"),
        ("callback", SendResult(False, 301, error="redirect not followed"), "retry"),
        ("callback", SendResult(False, 410), "dead"),
        ("callback", SendResult(False, error="refused: non-public address"), "dead"),
    ],
)
def test_outcome_table(kind, result, outcome):
    assert classify(kind, result) == outcome


def test_error_codes_are_short():
    assert error_code("telegram", SendResult(False, 403)) == "telegram 403"
    assert error_code("callback", SendResult(False, 503)) == "http 503"
    assert error_code("callback", SendResult(False, error="x" * 500)) == "x" * 200


# ---- callbacks -----------------------------------------------------------------------------


def test_callback_is_signed_at_send_time_and_verifiable(tmp_path):
    clock, sender = soon(), FakeWebhookSender()
    d, runs, outbox = dispatcher(tmp_path, clock, sender)
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    assert [m.event for m in outbox.for_run(run.id)] == ["research.completed"]
    assert d.tick() == 1
    url, body, headers = sender.posts[0]
    secret = callback_secret(KEY, run.id)
    assert url == HOOK and headers["user-agent"] == "TA-Research-Webhooks/1"
    assert headers["webhook-timestamp"] == str(int(clock.now.timestamp()))
    assert verify(
        secret, headers["webhook-id"], int(headers["webhook-timestamp"]), body,
        headers["webhook-signature"],
    )  # fmt: skip
    payload = json.loads(body)
    assert payload["type"] == "research.completed" and payload["data"]["run_id"] == run.id
    assert payload["data"]["links"] == {
        "self": f"https://api.example/research/{run.id}",
        "web": f"https://ui.example/?run={run.id}",
    }
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.DELIVERED and msg.last_status == 200 and msg.attempts == 1
    assert d.tick() == 0  # nothing due


def test_retry_then_delivered_with_the_same_id_and_body(tmp_path):
    clock = soon()
    sender = FakeWebhookSender(results=[SendResult(False, 503), SendResult(False, error="timeout")])
    d, runs, outbox = dispatcher(tmp_path, clock, sender)
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    d.tick()
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.PENDING and msg.attempts == 1
    assert msg.last_error == "http 503" and msg.last_status == 503
    assert msg.next_attempt_at == iso(clock.now + timedelta(seconds=10))
    assert d.tick() == 0  # not due yet
    clock.advance(10)
    d.tick()
    msg = only(outbox, run.id)
    assert msg.attempts == 2 and msg.last_error == "timeout"
    assert msg.next_attempt_at == iso(clock.now + timedelta(seconds=60))
    clock.advance(60)
    d.tick()
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.DELIVERED and msg.attempts == 3 and msg.last_error is None
    (_, b1, h1), (_, b2, h2), (_, b3, h3) = sender.posts
    assert b1 == b2 == b3 and h1["webhook-id"] == h2["webhook-id"] == h3["webhook-id"]
    assert h1["webhook-timestamp"] != h3["webhook-timestamp"]  # per attempt


def test_six_failures_go_dead(tmp_path):
    clock, sender = soon(), FakeWebhookSender(SendResult(False, 500))
    d, runs, outbox = dispatcher(tmp_path, clock, sender)
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    for delay in (0, 10, 60, 300, 1800, 7200):
        clock.advance(delay)
        assert d.tick() == 1
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.DEAD and msg.attempts == 6 and msg.last_error == "http 500"
    clock.advance(86400)
    assert d.tick() == 0 and len(sender.posts) == 6
    assert outbox.counts()["dead"] == 1


def test_max_attempts_setting_is_honored(tmp_path):
    clock, sender = soon(), FakeWebhookSender(SendResult(False, 500))
    d, runs, outbox = dispatcher(tmp_path, clock, sender, max_attempts=2)
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    d.tick()
    clock.advance(10)
    d.tick()
    assert only(outbox, run.id).status is OutboxStatus.DEAD


@pytest.mark.parametrize(
    ("result", "error"),
    [
        (SendResult(False, 410), "http 410"),
        (SendResult(False, error="refused: non-public address"), "refused: non-public address"),
    ],
)
def test_permanent_callback_failures_go_dead_at_once(tmp_path, result, error):
    d, runs, outbox = dispatcher(tmp_path, soon(), FakeWebhookSender(result))
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    d.tick()
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.DEAD and msg.attempts == 1 and msg.last_error == error


def test_a_message_whose_run_was_deleted_goes_dead(tmp_path):
    sender = FakeWebhookSender()
    d, runs, outbox = dispatcher(tmp_path, soon(), sender)
    outbox.add(OutboxMessage.new("run_gone", "callback", "research.completed", HOOK))
    d.tick()
    msg = only(outbox, "run_gone")
    assert msg.status is OutboxStatus.DEAD and msg.last_error == "run deleted"
    assert sender.posts == []


def test_a_crashing_render_is_retried_not_fatal(tmp_path):
    clock = soon()
    d, runs, outbox = dispatcher(tmp_path, clock)
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    d._view = lambda run: 1 / 0
    d.tick()
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.PENDING and msg.last_error == "error"


# ---- Telegram ------------------------------------------------------------------------------


def test_telegram_messages_bot_replies_and_rate_limit(tmp_path):
    clock = soon()
    telegram = FakeTelegram(results=[SendResult(False, 429, 7.0, "telegram 429")])
    d, runs, outbox = dispatcher(tmp_path, clock, telegram=telegram)
    outbox.add(
        OutboxMessage.new("", "telegram", EVENT_BOT_REPLY, "7", {"chat_id": "7", "text": "Hi"})
    )
    d.tick()  # the reply hits a 429 first
    clock.advance(6)
    assert d.tick() == 0  # retry_after (7 s) replaces the 10 s schedule
    clock.advance(1)
    assert d.tick() == 1
    assert telegram.sent == [("7", "Hi"), ("7", "Hi")]
    run = completed_run(runs, subscribers=[Subscriber("telegram", "42")])
    d.tick()
    chat, text = telegram.sent[-1]
    assert chat == "42" and "# T\n\nSummary." in text and f"?run={run.id}" in text
    assert only(outbox, run.id).status is OutboxStatus.DELIVERED


def test_retry_after_is_capped_at_two_hours(tmp_path):
    clock = soon()
    telegram = FakeTelegram(SendResult(False, 429, 99_999.0, "telegram 429"))
    d, runs, outbox = dispatcher(tmp_path, clock, telegram=telegram)
    run = completed_run(runs, subscribers=[Subscriber("telegram", "42")])
    d.tick()
    assert only(outbox, run.id).next_attempt_at == iso(clock.now + timedelta(hours=2))


def test_blocked_bot_goes_dead_at_once(tmp_path):
    telegram = FakeTelegram(SendResult(False, 403, None, "telegram 403"))
    d, runs, outbox = dispatcher(tmp_path, soon(), telegram=telegram)
    run = completed_run(runs, subscribers=[Subscriber("telegram", "42")])
    d.tick()
    msg = only(outbox, run.id)
    assert msg.status is OutboxStatus.DEAD and msg.last_error == "telegram 403"
    assert msg.last_status == 403


def test_pending_accepted_is_superseded_by_the_report(tmp_path):
    clock = soon()
    telegram = FakeTelegram(results=[SendResult(False, 502, None, "telegram 502")])
    d, runs, outbox = dispatcher(tmp_path, clock, telegram=telegram)
    run = ResearchRun.create("A research question", 5, False, False,
                             subscribers=[Subscriber("telegram", "42")])  # fmt: skip
    run.queue_outbox(OutboxMessage.new(run.id, "telegram", EVENT_ACCEPTED, "42", {"chat_id": "42"}))
    runs.create(run)
    d.tick()  # "working on it" fails once, waits 10 s
    run = runs.get(run.id)
    run.start()
    run.propose_final("# T\n\nDone.", forced=False, degraded=False)
    runs.save(run)
    clock.advance(30)  # both rows due: accepted (older) and completed
    d.tick()
    rows = {m.event: m for m in outbox.for_run(run.id)}
    assert rows[EVENT_ACCEPTED].status is OutboxStatus.DEAD
    assert rows[EVENT_ACCEPTED].last_error == "superseded" and rows[EVENT_ACCEPTED].attempts == 1
    assert rows["research.completed"].status is OutboxStatus.DELIVERED
    assert [t.startswith("Working on it") for _, t in telegram.sent] == [True, False]


@pytest.mark.parametrize("attempt", range(5))  # ids are random: the tie must not decide
def test_accepted_then_completed_in_order(tmp_path, attempt):
    telegram = FakeTelegram()
    d, runs, outbox = dispatcher(tmp_path, soon(), telegram=telegram)
    run = ResearchRun.create("A research question", 5, False, False,
                             subscribers=[Subscriber("telegram", "42")])  # fmt: skip
    run.queue_outbox(OutboxMessage.new(run.id, "telegram", EVENT_ACCEPTED, "42", {"chat_id": "42"}))
    runs.create(run)
    run.start()
    run.fail("boom")
    runs.save(run)
    assert d.tick() == 2
    assert telegram.sent[1][1].startswith("Sorry, the research failed: boom")
    assert telegram.sent[0][1].startswith("Working on it")


# ---- housekeeping and the thread -----------------------------------------------------------


def test_sweep_deletes_only_old_finished_rows_and_old_updates(tmp_path):
    clock = soon()
    ledger = SqliteUpdateLedger(str(tmp_path / "o.db"))
    d, runs, outbox = dispatcher(tmp_path, clock, ledger=ledger)
    old = clock.now - timedelta(days=40)
    done, pending = (OutboxMessage.new("r", "callback", "e", HOOK, now=old) for _ in range(2))
    fresh = OutboxMessage.new("r", "callback", "e", HOOK, now=clock.now)
    for m in (done, pending, fresh):
        outbox.add(m)
    outbox.mark_delivered(done.id, old)
    outbox.mark_dead(fresh.id, 1, "http 410")
    ledger.seen(1)
    assert d.sweep(clock.now) == {"outbox": 1, "updates": 0}
    assert {m.id for m in outbox.for_run("r")} == {pending.id, fresh.id}
    assert d.sweep(clock.now + timedelta(days=8))["updates"] == 1
    d_no_ledger, _, _ = dispatcher(tmp_path, clock)
    assert d_no_ledger.sweep() == {"outbox": 0, "updates": 0}


def test_background_thread_delivers_and_survives_errors(tmp_path):
    sender = FakeWebhookSender()
    d, runs, outbox = dispatcher(tmp_path, soon(), sender)
    calls = {"n": 0}
    real_due = outbox.due

    def flaky_due(now, limit):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real_due(now, limit)

    d._outbox.due = flaky_due
    run = completed_run(runs, subscribers=[Subscriber("callback", HOOK)])
    d.start(0.01)
    d.start(0.01)  # idempotent
    deadline = time.monotonic() + 3
    while not sender.posts and time.monotonic() < deadline:
        time.sleep(0.01)
    d.stop()
    assert len(sender.posts) == 1 and calls["n"] >= 2
    assert only(outbox, run.id).status is OutboxStatus.DELIVERED
    assert not any(t.name == "outbox" for t in threading.enumerate())


def test_a_callback_without_a_run_is_never_posted(tmp_path):
    sender = FakeWebhookSender()
    d, _, outbox = dispatcher(tmp_path, soon(), sender)
    outbox.add(OutboxMessage.new("", "callback", "research.completed", HOOK))
    d.tick()
    assert sender.posts == [] and only(outbox, "").last_error == "run deleted"
