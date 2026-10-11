"""Channels, subscribers and outbox messages (BACKEND_PLAN §5.6, v1.1 §24).

An outbox message is written in the SAME transaction as the run state that caused it, and
delivered later by the dispatcher (at least once). Bodies are rendered at SEND time from the
stored run; callback secrets are derived, never stored."""

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

# Delay before each attempt (attempt 1 at once): then dead.
RETRY_SCHEDULE_S: tuple[int, ...] = (0, 10, 60, 300, 1800, 7200)

EVENT_ACCEPTED = "research.accepted"  # Telegram only: "working on it"
EVENT_COMPLETED = "research.completed"
EVENT_FAILED = "research.failed"
EVENT_CANCELLED = "research.cancelled"
EVENT_BOT_REPLY = "bot.reply"  # standalone Telegram replies (/start, /help, refusals)


class Channel(StrEnum):
    WEB = "web"
    API = "api"
    TELEGRAM = "telegram"


@dataclass(frozen=True)
class Subscriber:
    kind: Literal["callback", "telegram"]
    target: str  # callback URL | Telegram chat id
    secret: str | None = None  # always None (v1.1): the callback secret is derived


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD = "dead"


def new_message_id() -> str:
    """Also the Standard Webhooks `webhook-id`."""
    return "msg_" + secrets.token_hex(12)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds")


@dataclass
class OutboxMessage:
    id: str
    run_id: str  # "" for standalone bot replies
    kind: Literal["callback", "telegram"]
    event: str
    target: str
    payload: dict[str, Any] = field(default_factory=dict)  # routing only; body rendered at send
    status: OutboxStatus = OutboxStatus.PENDING
    attempts: int = 0
    next_attempt_at: str = ""
    last_status: int | None = None
    last_error: str | None = None  # short, never a secret
    created_at: str = ""
    delivered_at: str | None = None

    @classmethod
    def new(
        cls,
        run_id: str,
        kind: Literal["callback", "telegram"],
        event: str,
        target: str,
        payload: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> "OutboxMessage":
        at = iso(now or datetime.now(UTC))
        return cls(
            new_message_id(),
            run_id,
            kind,
            event,
            target,
            dict(payload or {}),
            next_attempt_at=at,
            created_at=at,
        )


def next_attempt(attempts_made: int, now: datetime) -> str | None:
    """When to try again after `attempts_made` failed attempts; None = give up (dead)."""
    if attempts_made >= len(RETRY_SCHEDULE_S):
        return None
    return iso(now + timedelta(seconds=RETRY_SCHEDULE_S[attempts_made]))
