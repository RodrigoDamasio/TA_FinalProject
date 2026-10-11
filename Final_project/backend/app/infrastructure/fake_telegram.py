"""TELEGRAM_MODE=fake and tests: records messages instead of calling Telegram.
Owner: WEBHOOKS."""

from app.domain.ports import SendResult


class FakeTelegram:
    def __init__(self, result: SendResult | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.result = result or SendResult(True, 200)

    def send_message(self, chat_id: str, text: str) -> SendResult:
        self.sent.append((chat_id, text))
        return self.result
