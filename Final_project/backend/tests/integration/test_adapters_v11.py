"""Step 3 adapters with httpx.MockTransport and a fake resolver (0 network)."""

import httpx
import pytest

from app.domain.errors import FetchFailed, SearchUnavailable
from app.infrastructure.fake_search import FakePageFetcher, FakeWebSearch, demo_results
from app.infrastructure.fake_telegram import FakeTelegram
from app.infrastructure.html_extract import TrafilaturaExtractor
from app.infrastructure.safe_http import SafeResponse
from app.infrastructure.searxng import SearxngSearch
from app.infrastructure.telegram_client import TelegramClient
from app.infrastructure.webhook_sender import HttpWebhookSender

HTTPS = frozenset({"https"})


def resolver(addresses):
    def getaddrinfo(host, port, proto=0):
        return [(2, 1, 6, "", (a, port)) for a in addresses]

    return getaddrinfo


def test_searxng_adapter_maps_results_and_failures(tmp_path, caplog):
    from app.infrastructure.sqlite_store import SqliteSearchCache

    replies = []

    def handler(request):
        assert request.url.params["format"] == "json"
        reply = replies.pop(0)
        return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

    now = {"t": 0.0}
    s = SearxngSearch(
        "http://searxng:8080/", 5, SqliteSearchCache(str(tmp_path / "c.db")),
        clock=lambda: now["t"], client=httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    replies.append({"results": [
        {"title": "", "url": "https://b.org", "content": ""},
        {"title": " A ", "url": "https://a.org/p#frag", "content": " snip ", "engines": ["yahoo"]},
        {"title": "dup", "url": "https://a.org/p", "content": "again"},
        {"title": "x", "url": "ftp://x", "content": "no"},
    ], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})  # fmt: skip
    caplog.set_level("INFO")
    results = s.search("secret query text", 8)
    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("A", "https://a.org/p", "snip"), ("b.org", "https://b.org", ""),
    ]  # fmt: skip
    assert results[0].engines == ("yahoo",) and "secret query text" not in caplog.text
    assert s.search("SECRET   query text", 8) == results  # cache hit: no request queued
    replies.append({"results": [], "unresponsive_engines": []})
    assert s.search("nothing", 8) == []  # empty: engines answered, not an outage
    replies.append({"results": [], "unresponsive_engines": []})
    assert s.search("nothing", 8) == []  # empty results are never cached
    for reply in (
        {
            "results": [],
            "unresponsive_engines": [["yahoo", "x"], ["yandex", "x"], ["duckduckgo", "x"]],
        },
        httpx.Response(403),
        httpx.Response(200, content=b"not json"),
    ):
        replies.append(reply)
        with pytest.raises(SearchUnavailable):
            s.search("blocked", 8)
    assert not s.available()  # 3 failures: breaker open
    with pytest.raises(SearchUnavailable, match="breaker"):
        s.search("other", 8)
    assert s.search("secret query text", 8) == results  # cached queries still answer
    now["t"] = 61
    assert s.available() and s.stats()["failures"] == 3


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

    class Guarded:
        def __init__(self, outcome):
            self.outcome = outcome

        def request(self, method, url, policy, **kw):
            if isinstance(self.outcome, Exception):
                raise self.outcome
            return SafeResponse(url, self.outcome)

    assert HttpWebhookSender(5, client=Guarded(204)).post("https://a.org/h", b"{}", {}).ok
    timeout = HttpWebhookSender(5, client=Guarded(FetchFailed("timeout")))
    assert timeout.post("https://a.org", b"", {}).error == "timeout"
    refused = HttpWebhookSender(5).post("http://example.com/h", b"{}", {})  # https only
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
    text = TrafilaturaExtractor().html_to_markdown(
        "<html><body><nav>Menu</nav><article><h1>Title</h1><script>x()</script><p>Body &amp; "
        "more text about retrieval augmented generation and its trade-offs.</p></article>"
        "</body></html>", "https://a.org"
    )  # fmt: skip
    assert "Body & more" in text and "x()" not in text and "Menu" not in text
    assert TrafilaturaExtractor().html_to_markdown("  ", "u") == ""
