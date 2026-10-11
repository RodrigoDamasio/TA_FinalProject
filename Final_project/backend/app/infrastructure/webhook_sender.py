"""Outbound callback POSTs through the SSRF guard (SafeHttpClient): the address is checked on
EVERY delivery and the connection goes to the vetted IP (no DNS rebinding after submit);
redirects are never followed. Owner: WEBHOOKS (transport from SEARXNG's safe_http)."""

from app.domain.errors import FetchFailed, FetchRefused
from app.domain.ports import SendResult

from .safe_http import SafeHttpClient, callback_policy


class HttpWebhookSender:
    def __init__(
        self, timeout_s: float, allow_private: bool = False, client: SafeHttpClient | None = None
    ) -> None:
        self._policy = callback_policy(allow_private)
        self._client = client or SafeHttpClient(timeout_s, 64 * 1024, 0)

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        try:
            response = self._client.request(
                "POST", url, self._policy, content=body, headers=headers
            )
        except FetchRefused as err:
            return SendResult(False, error=f"refused: {err.reason}")
        except FetchFailed as err:
            return SendResult(False, error=err.reason)
        return SendResult(200 <= response.status < 300, response.status)
