"""Step 3 adapters with httpx.MockTransport and a fake resolver (0 network)."""

import httpx
import pytest

from app.domain.errors import FetchFailed, FetchRefused, SearchUnavailable
from app.infrastructure import safe_http
from app.infrastructure.fake_search import FakePageFetcher, FakeWebSearch, demo_results
from app.infrastructure.fake_telegram import FakeTelegram
from app.infrastructure.html_extract import TagStripExtractor
from app.infrastructure.searxng import SearxngSearch
from app.infrastructure.telegram_client import TelegramClient
from app.infrastructure.webhook_sender import HttpWebhookSender

HTTPS = frozenset({"https"})


def resolver(addresses):
    def getaddrinfo(host, port, proto=0):
        return [(2, 1, 6, "", (a, port)) for a in addresses]

    return getaddrinfo


def test_url_check_rules(monkeypatch):
    monkeypatch.setattr(safe_http.socket, "getaddrinfo", resolver(["93.184.216.34"]))
    safe_http.check_url("https://example.com/hook", schemes=HTTPS)
    for url, reason in (
        ("http://example.com", "scheme"),
        ("https://u:p@example.com", "credentials"),
        ("https://example.com:8443", "ports"),
        ("https://svc.internal/x", "public name"),
        ("https://" + "a" * 2050, "too long"),
    ):
        with pytest.raises(FetchRefused, match=reason):
            safe_http.check_url(url, schemes=HTTPS)
    for private in ("10.0.0.1", "169.254.169.254", "::1", "fd00::1"):
        monkeypatch.setattr(safe_http.socket, "getaddrinfo", resolver(["93.184.216.34", private]))
        with pytest.raises(FetchRefused, match="non-public"):
            safe_http.check_url("https://example.com", schemes=HTTPS)
    safe_http.check_url("http://localhost.example:80/x", schemes=frozenset({"http"}),
                        allow_private=True)  # fmt: skip

    def fail(*a, **k):
        raise OSError("nxdomain")

    monkeypatch.setattr(safe_http.socket, "getaddrinfo", fail)
    with pytest.raises(FetchRefused, match="does not resolve"):
        safe_http.check_url("https://nope.example", schemes=HTTPS)
    with pytest.raises(FetchFailed):
        safe_http.StubPageFetcher().fetch("https://a.org")


def test_searxng_adapter_maps_results_and_failures():
    def handler(request):
        assert request.url.params["format"] == "json"
        return httpx.Response(200, json={"results": [
            {"title": " A ", "url": "https://a.org", "content": "snip", "engines": ["yahoo"]},
            {"title": "", "url": "ftp://x", "content": ""},
            {"title": "", "url": "https://b.org", "content": ""},
        ]})  # fmt: skip

    s = SearxngSearch("http://searxng:8080/", 5)
    s._client = httpx.Client(transport=httpx.MockTransport(handler))
    results = s.search("rag", 8)
    assert [(r.title, r.url) for r in results] == [
        ("A", "https://a.org"),
        ("https://b.org", "https://b.org"),
    ]
    assert results[0].engines == ("yahoo",) and s.available()
    s._client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403)))
    with pytest.raises(SearchUnavailable):
        s.search("rag", 8)


def test_telegram_client_and_webhook_sender(monkeypatch):
    tg = TelegramClient("123:token", 5)
    tg._client = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, json={"parameters": {"retry_after": 7}})))  # fmt: skip
    limited = tg.send_message("42", "hi")
    assert not limited.ok and limited.retry_after_s == 7
    tg._client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    assert tg.send_message("42", "hi").ok

    def boom(request):
        raise httpx.ConnectError("down")

    tg._client = httpx.Client(transport=httpx.MockTransport(boom))
    assert tg.send_message("42", "hi").error == "ConnectError"

    sender = HttpWebhookSender(5, allow_private=True)
    sender._client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(204)))
    assert sender.post("http://localhost.test/h", b"{}", {}).ok
    sender._client = httpx.Client(transport=httpx.MockTransport(boom))
    assert sender.post("http://localhost.test/h", b"{}", {}).error == "ConnectError"
    strict = HttpWebhookSender(5)
    refused = strict.post("http://example.com/h", b"{}", {})
    assert not refused.ok and refused.error.startswith("refused:")
    fake = FakeTelegram()
    assert fake.send_message("1", "x").ok and fake.sent == [("1", "x")]


def test_fakes_and_html_extraction():
    search = FakeWebSearch(fail=True)
    assert not search.available()
    with pytest.raises(SearchUnavailable):
        search.search("q", 3)
    assert len(demo_results("rag", 2)) == 2 and FakeWebSearch().search("q", 1)[0].url
    page = FakePageFetcher().fetch(demo_results("rag")[0].url)
    assert page.status == 200 and "Demo page" in page.text
    text = TagStripExtractor().html_to_markdown(
        "<html><script>x()</script><h1>Title</h1><p>Body &amp; more</p></html>", "u"
    )
    assert text == "Title\n\nBody & more"
