"""Outbound callback POSTs (WEBHOOKS_PLAN §3.5, §7) through the SSRF guard (SEARXNG's
`SafeHttpClient`): the URL and every resolved address are checked on EVERY delivery, and the
connection goes to the vetted IP, so a host that rebinds to a private address after submit is
refused. Redirects are never followed, and at most 64 KB of the response is read and
discarded. Errors are short codes, never the URL or the response body. Owner: WEBHOOKS."""

from email.utils import parsedate_to_datetime
from time import time
from typing import Any, Protocol

from app.domain.errors import FetchFailed, FetchRefused
from app.domain.ports import SendResult

from .safe_http import SafeHttpClient, SafeResponse, UrlPolicy, callback_policy

RESPONSE_READ_MAX = 64 * 1024


class GuardedClient(Protocol):
    def request(self, method: Any, url: str, policy: UrlPolicy, **kw: Any) -> SafeResponse: ...


def _retry_after(value: str | None, now: float) -> float | None:
    """`Retry-After` as seconds or an HTTP date; None when absent or unreadable."""
    if not value:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError):
        return None


class HttpWebhookSender:
    def __init__(
        self, timeout_s: float, allow_private: bool = False, *, client: GuardedClient | None = None
    ) -> None:
        self._policy = callback_policy(allow_private)
        self._client = client or SafeHttpClient(timeout_s, RESPONSE_READ_MAX, 0)

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        try:
            response = self._client.request(
                "POST", url, self._policy, content=body, headers=headers, discard_body=True
            )
        except FetchRefused as err:
            return SendResult(False, error=f"refused: {err.reason}")
        except FetchFailed as err:
            return SendResult(False, error="timeout" if err.reason == "timeout" else "connect")
        status = response.status
        if 200 <= status < 300:
            return SendResult(True, status)
        if 300 <= status < 400:
            return SendResult(False, status, error="redirect not followed")
        retry = _retry_after(response.headers.get("retry-after"), time()) if status == 429 else None
        return SendResult(False, status, retry, f"http {status}")
