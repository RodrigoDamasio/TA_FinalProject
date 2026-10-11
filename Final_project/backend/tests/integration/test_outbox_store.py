"""SqliteOutboxRepository and SqliteUpdateLedger against a temp database (WEBHOOKS_PLAN §9
integration). They live in the Shared sqlite_store.py: the architecture test keeps all SQL
there."""

import threading
from datetime import UTC, datetime, timedelta

from app.domain.outbox import OutboxMessage, OutboxStatus, iso
from app.infrastructure.sqlite_store import SqliteOutboxRepository, SqliteUpdateLedger

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def msg(run_id="run_1", at=NOW, kind="callback", event="research.completed"):
    return OutboxMessage.new(run_id, kind, event, "https://a.example/h", {"k": "v"}, now=at)


def test_due_order_limit_and_status(tmp_path):
    repo = SqliteOutboxRepository(str(tmp_path / "o.db"))
    late, early = msg(at=NOW), msg(at=NOW - timedelta(minutes=5))
    future = msg(at=NOW + timedelta(days=1))
    for m in (late, early, future):
        repo.add(m)
    assert [m.id for m in repo.due(NOW, 10)] == [early.id, late.id]
    assert [m.id for m in repo.due(NOW, 1)] == [early.id]
    repo.mark_retry(early.id, 1, iso(NOW + timedelta(seconds=10)), "http 503", 503)
    assert [m.id for m in repo.due(NOW, 10)] == [late.id]
    repo.mark_delivered(late.id, NOW, 204)
    repo.mark_dead(future.id, 6, "timeout")
    assert repo.due(NOW + timedelta(days=2), 10)[0].id == early.id
    rows = {m.id: m for m in repo.for_run("run_1")}
    assert rows[early.id].attempts == 1 and rows[early.id].last_status == 503
    assert rows[early.id].payload == {"k": "v"} and rows[early.id].last_error == "http 503"
    assert rows[late.id].status is OutboxStatus.DELIVERED and rows[late.id].attempts == 1
    assert rows[late.id].delivered_at == iso(NOW) and rows[late.id].last_status == 204
    assert rows[future.id].status is OutboxStatus.DEAD and rows[future.id].last_error == "timeout"
    assert repo.for_run("run_other") == []


def test_values_are_bound_never_interpolated(tmp_path):
    repo = SqliteOutboxRepository(str(tmp_path / "o.db"))
    evil = "run_1'; DROP TABLE outbox; --"
    repo.add(msg(run_id=evil))
    assert len(repo.for_run(evil)) == 1 and repo.counts()["pending"] == 1


def test_retention_and_counts(tmp_path):
    repo = SqliteOutboxRepository(str(tmp_path / "o.db"))
    old_done, old_pending, new_dead = (msg(at=NOW - timedelta(days=40)) for _ in range(3))
    new_dead.created_at = iso(NOW)
    for m in (old_done, old_pending, new_dead):
        repo.add(m)
    repo.mark_delivered(old_done.id, NOW)
    repo.mark_dead(new_dead.id, 1, "telegram 403", 403)
    assert repo.delete_older_than(iso(NOW - timedelta(days=30))) == 1  # pending rows stay
    counts = repo.counts()
    assert counts["pending"] == 1 and counts["dead"] == 1 and counts["oldest_pending_s"] > 0


def test_update_ledger_is_atomic_under_threads(tmp_path):
    ledger = SqliteUpdateLedger(str(tmp_path / "l.db"))
    results: list[bool] = []
    barrier = threading.Barrier(4)

    def receive() -> None:
        barrier.wait()
        results.append(ledger.seen(777))

    threads = [threading.Thread(target=receive) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, True, True, True]  # exactly one first delivery
    assert ledger.forget_older_than(iso(NOW - timedelta(days=7))) == 0  # received just now
    assert ledger.forget_older_than("2999-01-01T00:00:00+00:00") == 1
    assert ledger.seen(777) is False
