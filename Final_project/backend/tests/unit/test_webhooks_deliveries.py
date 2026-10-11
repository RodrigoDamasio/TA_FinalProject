"""DeliveryView mapping hides the secret, the payload, the URL path and the chat id
(WEBHOOKS_PLAN §3.7, §9 unit)."""

import json
from datetime import UTC, datetime

from app.application.outbox.deliveries import deliveries, delivery_view
from app.domain.outbox import EVENT_BOT_REPLY, OutboxMessage, OutboxStatus

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


class Outbox:
    def __init__(self, rows):
        self.rows = rows

    def for_run(self, run_id):
        return [m for m in self.rows if m.run_id == run_id]


def test_callback_view_shows_the_host_only():
    msg = OutboxMessage.new(
        "run_1", "callback", "research.completed",
        "https://hooks.example.com:443/path/TOKEN123?key=SECRET", now=NOW,
    )  # fmt: skip
    view = delivery_view(msg)
    assert view == {
        "id": msg.id,
        "kind": "callback",
        "event": "research.completed",
        "target_host": "hooks.example.com",
        "status": "pending",
        "attempts": 0,
        "last_status": None,
        "last_error": None,
        "next_attempt_at": "2026-10-11T12:00:00+00:00",
        "delivered_at": None,
    }
    assert "TOKEN123" not in json.dumps(view) and "SECRET" not in json.dumps(view)


def test_telegram_view_never_shows_the_chat_and_finished_rows_have_no_next_attempt():
    msg = OutboxMessage.new(
        "run_1", "telegram", "research.completed", "987654", {"chat_id": "987654"}
    )
    msg.status, msg.attempts, msg.last_status = OutboxStatus.DELIVERED, 1, 200
    msg.delivered_at = "2026-10-11T12:00:05+00:00"
    view = delivery_view(msg)
    assert view["target_host"] == "api.telegram.org" and "987654" not in json.dumps(view)
    assert view["next_attempt_at"] is None and view["delivered_at"].endswith("05+00:00")


def test_deliveries_skip_bot_replies():
    rows = [
        OutboxMessage.new("run_1", "callback", "research.completed", "https://a.example/h"),
        OutboxMessage.new("run_1", "telegram", EVENT_BOT_REPLY, "1", {"text": "x"}),
        OutboxMessage.new("run_2", "callback", "research.completed", "https://b.example/h"),
    ]
    views = deliveries(Outbox(rows), "run_1")
    assert [v["id"] for v in views] == [rows[0].id]
