"""Telegram inbound updates → RunService (step 3 STUB). WEBHOOKS_PLAN implements commands,
the chat allowlist, the update ledger, per-chat limits and the replies. Owner: WEBHOOKS."""

from typing import Any

from app.domain.errors import FeatureNotReady


class TelegramService:
    def handle(self, update: dict[str, Any]) -> None:
        raise FeatureNotReady("The Telegram bot")
