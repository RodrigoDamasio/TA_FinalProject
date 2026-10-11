"""TELEGRAM_MODE=fake and tests: records messages instead of calling Telegram. `results`
(consumed in order) or `result` decide each outcome: status, retry_after, errors.
Owner: WEBHOOKS."""

from app.domain.ports import SendResult


class FakeTelegram:
    def __init__(self, result: SendResult | None = None, results: list | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.result = result or SendResult(True, 200)
        self.results = list(results or [])

    def send_message(self, chat_id: str, text: str) -> SendResult:
        self.sent.append((chat_id, text))
        return self.results.pop(0) if self.results else self.result
