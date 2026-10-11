"""Retry schedule (WEBHOOKS_PLAN §3.7): attempts at 0 s, +10 s, +1 min, +5 min, +30 min, +2 h,
then dead. No jitter: one sender, nothing to de-synchronize. Owner: WEBHOOKS."""

from datetime import datetime, timedelta

from app.domain.outbox import RETRY_SCHEDULE_S, iso

MAX_RETRY_AFTER_S = 7200.0  # a 429's Retry-After is honored up to 2 h


def retry_delay_s(attempts_made: int, max_attempts: int) -> int | None:
    """Seconds to wait after `attempts_made` failed attempts; None = give up (dead)."""
    if attempts_made >= max_attempts or attempts_made >= len(RETRY_SCHEDULE_S):
        return None
    return RETRY_SCHEDULE_S[attempts_made]


def retry_at(
    attempts_made: int, now: datetime, max_attempts: int, retry_after_s: float | None = None
) -> str | None:
    """ISO time of the next attempt, or None when the message is dead. A 429's
    `retry_after_s` replaces the schedule's delay (capped at 2 h), not the attempt limit."""
    delay = retry_delay_s(attempts_made, max_attempts)
    if delay is None:
        return None
    if retry_after_s is not None:
        return iso(now + timedelta(seconds=min(max(retry_after_s, 0.0), MAX_RETRY_AFTER_S)))
    return iso(now + timedelta(seconds=delay))
