"""Telegram Bot API `sendMessage` (WEBHOOKS_PLAN §3.4, §7). Plain text, no parse mode, link
previews off. The token is part of the URL, so URLs are never logged: the `httpx` and
`httpcore` loggers are raised to WARNING (they log request URLs at INFO), and exceptions are
mapped to short codes, never their text. Owner: WEBHOOKS."""

import logging

import httpx

from app.domain.ports import SendResult

API_BASE = "https://api.telegram.org"
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)


def _retry_after(response: httpx.Response, body: dict) -> float | None:
    params = body.get("parameters")
    if isinstance(params, dict) and isinstance(params.get("retry_after"), int | float):
        return float(params["retry_after"])
    header = response.headers.get("retry-after", "")
    return float(header) if header.isdigit() else None


class TelegramClient:
    def __init__(self, token: str, timeout_s: float, *, client: httpx.Client | None = None) -> None:
        self._url = f"{API_BASE}/bot{token}/sendMessage"
        self._client = client or httpx.Client(timeout=timeout_s, trust_env=False)

    def send_message(self, chat_id: str, text: str) -> SendResult:
        payload = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
        try:
            response = self._client.post(self._url, json=payload)
        except httpx.TimeoutException:
            return SendResult(False, error="timeout")
        except httpx.HTTPError:
            return SendResult(False, error="connect")
        try:
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        status = response.status_code
        if status == 200 and body.get("ok", True) is True:
            return SendResult(True, status)
        retry = _retry_after(response, body) if status == 429 else None
        return SendResult(False, status, retry, f"telegram {status}")
