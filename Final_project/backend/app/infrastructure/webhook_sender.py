"""Outbound callback POSTs (WEBHOOKS_PLAN §3.5, §7). The URL goes through the SSRF guard on
EVERY delivery (a host that resolves to a private address later is refused), redirects are
never followed, and at most 64 KB of the response is read and discarded. Errors are short
codes, never the URL or the response body. Owner: WEBHOOKS.

The connection itself is a plain httpx client until SEARXNG's `SafeHttpClient` (connect to
the vetted IP, SEARXNG_TOOL_PLAN §3.9) lands; `client` is injectable for that swap."""

from collections.abc import Callable
from email.utils import parsedate_to_datetime
from time import time

import httpx

from app.domain.errors import FetchRefused
from app.domain.ports import SendResult

from .safe_http import check_url

RESPONSE_READ_MAX = 64 * 1024


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
        self,
        timeout_s: float,
        allow_private: bool = False,
        *,
        client: httpx.Client | None = None,
        check: Callable[..., None] = check_url,
    ) -> None:
        self._allow_private, self._check = allow_private, check
        self._schemes = frozenset({"http", "https"} if allow_private else {"https"})
        self._client = client or httpx.Client(
            timeout=timeout_s, follow_redirects=False, trust_env=False
        )

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        try:
            self._check(url, schemes=self._schemes, allow_private=self._allow_private)
        except FetchRefused as err:
            return SendResult(False, error=f"refused: {err.reason}")
        try:
            with self._client.stream(
                "POST", url, content=body, headers=headers, follow_redirects=False
            ) as response:
                read = 0
                for chunk in response.iter_bytes():
                    read += len(chunk)
                    if read >= RESPONSE_READ_MAX:
                        break
        except httpx.TimeoutException:
            return SendResult(False, error="timeout")
        except httpx.HTTPError:
            return SendResult(False, error="connect")
        status = response.status_code
        if 200 <= status < 300:
            return SendResult(True, status)
        if 300 <= status < 400:
            return SendResult(False, status, error="redirect not followed")
        retry = _retry_after(response.headers.get("retry-after"), time()) if status == 429 else None
        return SendResult(False, status, retry, f"http {status}")
