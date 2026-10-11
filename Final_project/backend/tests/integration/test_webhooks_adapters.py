"""TelegramClient and HttpWebhookSender against httpx.MockTransport, FakeTelegram
(WEBHOOKS_PLAN §9 integration). 0 network: the sender runs over a guarded-client fake that
applies the real URL rules and stands in for the address check (the SafeHttpClient itself is
tested in tests/unit/test_safe_http_client.py)."""

import json
import logging

import httpx
import pytest

from app.domain.errors import FetchFailed, FetchRefused
from app.domain.ports import SendResult
from app.infrastructure.fake_telegram import FakeTelegram
from app.infrastructure.safe_http import SafeHttpClient, SafeResponse, check_url
from app.infrastructure.telegram_client import TelegramClient
from app.infrastructure.webhook_sender import RESPONSE_READ_MAX, HttpWebhookSender, _retry_after

TOKEN = "123456:CANARY-token-never-logged"


def telegram(handler) -> TelegramClient:
    return TelegramClient(TOKEN, 5, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_telegram_sends_plain_text_without_previews():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    assert telegram(handler).send_message("42", "Hello <b>x</b>") == SendResult(True, 200)
    body = json.loads(seen[0].content)
    assert body == {
        "chat_id": "42",
        "text": "Hello <b>x</b>",
        "link_preview_options": {"is_disabled": True},
    }
    assert "parse_mode" not in body and seen[0].url.path == f"/bot{TOKEN}/sendMessage"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 7}}),
         SendResult(False, 429, 7.0, "telegram 429")),
        (httpx.Response(429, headers={"Retry-After": "12"}, json={"ok": False}),
         SendResult(False, 429, 12.0, "telegram 429")),
        (httpx.Response(429, text="not json"), SendResult(False, 429, None, "telegram 429")),
        (httpx.Response(403, json={"ok": False, "description": "bot was blocked"}),
         SendResult(False, 403, None, "telegram 403")),
        (httpx.Response(400, json=["odd"]), SendResult(False, 400, None, "telegram 400")),
        (httpx.Response(200, json={"ok": False}), SendResult(False, 200, None, "telegram 200")),
        (httpx.Response(502, text="bad gateway"), SendResult(False, 502, None, "telegram 502")),
    ],
)  # fmt: skip
def test_telegram_error_mapping(response, expected):
    assert telegram(lambda r: response).send_message("42", "hi") == expected


def test_telegram_network_errors_never_leak_the_token(caplog):
    def timeout(request):
        raise httpx.ReadTimeout(f"timed out on {request.url}")

    def down(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    caplog.set_level(logging.DEBUG)
    assert telegram(timeout).send_message("42", "hi") == SendResult(False, error="timeout")
    assert telegram(down).send_message("42", "hi") == SendResult(False, error="connect")
    telegram(lambda r: httpx.Response(200, json={"ok": True})).send_message("42", "hi")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert "CANARY" not in caplog.text


class Check:
    """Stands in for the address check: refuses every host when told to."""

    def __init__(self, refuse: bool = False) -> None:
        self.refuse, self.calls = refuse, []


class Guarded:
    """The SafeHttpClient interface over httpx.MockTransport: real URL rules (check_url with
    the sender's policy) on every request, then the fake address check, then the handler."""

    def __init__(self, handler, check: Check) -> None:
        self._client = httpx.Client(transport=httpx.MockTransport(handler))
        self.check, self.kw = check, []

    def request(self, method, url, policy, **kw):
        self.kw.append(kw)
        check_url(url, policy)
        self.check.calls.append((url, policy.schemes, policy.allow_private))
        if self.check.refuse:
            raise FetchRefused("the host resolves to a non-public address")
        try:
            r = self._client.request(method, url, content=kw.get("content"),
                                     headers=kw.get("headers"))  # fmt: skip
        except httpx.TimeoutException as err:
            raise FetchFailed("timeout") from err
        except httpx.HTTPError as err:
            raise FetchFailed("connection failed") from err
        return SafeResponse(url, r.status_code, {k.lower(): v for k, v in r.headers.items()})


def sender(handler, check=None, allow_private=False) -> HttpWebhookSender:
    return HttpWebhookSender(5, allow_private, client=Guarded(handler, check or Check()))


def test_sender_posts_body_and_headers_and_checks_every_time():
    seen, check = [], Check()

    def handler(request):
        seen.append(request)
        return httpx.Response(204)

    s = sender(handler, check)
    for _ in range(2):
        assert s.post("https://hooks.example/h", b'{"a":1}', {"webhook-id": "msg_1"}).ok
    assert len(check.calls) == 2 and check.calls[0][1] == frozenset({"https"})
    assert seen[0].content == b'{"a":1}' and seen[0].headers["webhook-id"] == "msg_1"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (httpx.Response(301, headers={"Location": "https://other.example/"}),
         SendResult(False, 301, None, "redirect not followed")),
        (httpx.Response(410), SendResult(False, 410, None, "http 410")),
        (httpx.Response(503), SendResult(False, 503, None, "http 503")),
        (httpx.Response(429, headers={"Retry-After": "30"}),
         SendResult(False, 429, 30.0, "http 429")),
        (httpx.Response(429), SendResult(False, 429, None, "http 429")),
    ],
)  # fmt: skip
def test_sender_outcomes(response, expected):
    assert sender(lambda r: response).post("https://hooks.example/h", b"{}", {}) == expected


def test_sender_ssrf_refusal_timeout_and_connect():
    hits = []
    refused = sender(lambda r: hits.append(r) or httpx.Response(200), Check(refuse=True))
    result = refused.post("https://rebound.example/h", b"{}", {})
    assert result == SendResult(False, error="refused: the host resolves to a non-public address")
    assert hits == []  # nothing was sent

    def timeout(request):
        raise httpx.ConnectTimeout("slow")

    def down(request):
        raise httpx.ConnectError("down")

    assert sender(timeout).post("https://h.example/", b"", {}).error == "timeout"
    assert sender(down).post("https://h.example/", b"", {}).error == "connect"


def test_sender_reads_at_most_64_kb_of_the_response():
    s = sender(lambda r: httpx.Response(200))
    assert s.post("https://h.example/", b"", {}).ok
    assert s._client.kw[0]["discard_body"] is True  # a big answer is drained, not an error
    default = HttpWebhookSender(5)._client
    assert isinstance(default, SafeHttpClient) and default._max_bytes == RESPONSE_READ_MAX
    assert default._max_redirects == 0


def test_sender_private_mode_allows_http_and_retry_after_dates():
    check = Check()
    s = sender(lambda r: httpx.Response(200), check, allow_private=True)
    assert s.post("http://localhost:8001/hook", b"", {}).ok
    assert check.calls[0][1:] == (frozenset({"http", "https"}), True)
    assert _retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 1445412480.0) == 0.0
    assert _retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 1445412000.0) == 480.0
    assert _retry_after("soon", 0) is None and _retry_after(None, 0) is None


def test_fake_telegram_is_programmable():
    fake = FakeTelegram(results=[SendResult(False, 429, 3.0, "telegram 429")])
    assert fake.send_message("1", "a").retry_after_s == 3.0
    assert fake.send_message("1", "b").ok and fake.sent == [("1", "a"), ("1", "b")]
