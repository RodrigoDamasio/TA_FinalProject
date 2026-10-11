"""Telegram Bot API sendMessage (step 3 version). The token is part of the URL, so URLs are
never logged. WEBHOOKS_PLAN adds retry_after handling and error mapping. Owner: WEBHOOKS."""

import httpx

from app.domain.ports import SendResult


class TelegramClient:
    def __init__(self, token: str, timeout_s: float) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._client = httpx.Client(timeout=timeout_s, trust_env=False)

    def send_message(self, chat_id: str, text: str) -> SendResult:
        try:
            response = self._client.post(self._url, json={"chat_id": chat_id, "text": text})
        except httpx.HTTPError as err:
            return SendResult(False, error=type(err).__name__)
        retry = None
        if response.status_code == 429:
            retry = float(response.json().get("parameters", {}).get("retry_after", 30))
        return SendResult(response.status_code == 200, response.status_code, retry)
