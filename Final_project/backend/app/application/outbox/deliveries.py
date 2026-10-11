"""`DeliveryView` (BACKEND_PLAN §14.5, WEBHOOKS_PLAN §3.7): what `GET /research/{id}/deliveries`
shows. No payload, no secret, no URL path or query (they can hold tokens), never a chat id:
the host only. Owner: WEBHOOKS."""

from typing import Any
from urllib.parse import urlsplit

from app.domain.outbox import EVENT_BOT_REPLY, OutboxMessage, OutboxStatus
from app.domain.ports import OutboxRepository

TELEGRAM_HOST = "api.telegram.org"


def target_host(msg: OutboxMessage) -> str | None:
    if msg.kind == "telegram":
        return TELEGRAM_HOST
    return urlsplit(msg.target).hostname


def delivery_view(msg: OutboxMessage) -> dict[str, Any]:
    pending = msg.status is OutboxStatus.PENDING
    return {
        "id": msg.id,
        "kind": msg.kind,
        "event": msg.event,
        "target_host": target_host(msg),
        "status": msg.status.value,
        "attempts": msg.attempts,
        "last_status": msg.last_status,
        "last_error": msg.last_error,
        "next_attempt_at": msg.next_attempt_at if pending else None,
        "delivered_at": msg.delivered_at,
    }


def deliveries(outbox: OutboxRepository, run_id: str) -> list[dict[str, Any]]:
    """The run's notifications, oldest first. Standalone bot replies have no run."""
    return [delivery_view(m) for m in outbox.for_run(run_id) if m.event != EVENT_BOT_REPLY]
