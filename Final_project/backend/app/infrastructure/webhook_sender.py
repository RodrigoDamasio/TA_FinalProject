"""Outbound callback POSTs (step 3 version): the URL is re-checked by the SSRF guard on every
delivery and redirects are never followed. WEBHOOKS + SEARXNG replace the transport with
safe_http's SafeHttpClient (connect to the vetted IP). Owner: WEBHOOKS."""

import httpx

from app.domain.errors import FetchRefused
from app.domain.ports import SendResult

from .safe_http import check_url


class HttpWebhookSender:
    def __init__(self, timeout_s: float, allow_private: bool = False) -> None:
        self._allow_private = allow_private
        self._schemes = frozenset({"http", "https"} if allow_private else {"https"})
        self._client = httpx.Client(timeout=timeout_s, follow_redirects=False, trust_env=False)

    def post(self, url: str, body: bytes, headers: dict[str, str]) -> SendResult:
        try:
            check_url(url, schemes=self._schemes, allow_private=self._allow_private)
            response = self._client.post(url, content=body, headers=headers)
        except FetchRefused as err:
            return SendResult(False, error=f"refused: {err.reason}")
        except httpx.HTTPError as err:
            return SendResult(False, error=type(err).__name__)
        return SendResult(200 <= response.status_code < 300, response.status_code)
