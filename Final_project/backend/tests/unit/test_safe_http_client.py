"""SafeHttpClient over a fake network backend (SEARXNG_TOOL_PLAN §9): no network."""

import gzip

import httpcore
import pytest

from app.domain.errors import FetchFailed, FetchRefused
from app.infrastructure.safe_http import (
    PAGE_POLICY,
    GuardedTransport,
    SafeHttpClient,
    SafeHttpFetcher,
)


class Stream(httpcore.NetworkStream):
    def __init__(self, net, address):
        self.net, self.address, self.out = net, address, []

    def write(self, buffer, timeout=None):
        if b"\r\n\r\n" in buffer and not self.out:
            request = buffer.decode("latin-1")
            self.net.requests.append((self.address, request))
            self.out = list(self.net.respond(self.address, request))

    def read(self, max_bytes, timeout=None):
        return self.out.pop(0) if self.out else b""

    def close(self):
        pass

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self.net.tls_names.append(server_hostname)
        return self

    def get_extra_info(self, info):
        return None


class Net(httpcore.NetworkBackend):
    """Records the address each connection goes to; `routes` maps a request path to a reply."""

    def __init__(self, routes):
        self.routes, self.connected, self.requests, self.tls_names = routes, [], [], []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append((host, port))
        return Stream(self, host)

    def respond(self, address, request):
        path = request.split(" ", 2)[1]
        reply = self.routes[path]
        return reply if isinstance(reply, list) else [reply]


def http(status, body=b"", **headers):
    head = {
        "content-length": str(len(body)),
        **{k.replace("_", "-"): v for k, v in headers.items()},
    }
    lines = "".join(f"{k}: {v}\r\n" for k, v in head.items())
    return f"HTTP/1.1 {status} X\r\n{lines}\r\n".encode() + body


def resolver(table):
    calls = []

    def resolve(host, port):
        calls.append(host)
        answer = table[host]
        return answer.pop(0) if answer and isinstance(answer[0], list) else answer

    resolve.calls = calls
    return resolve


def client(routes, table=None, max_bytes=10_000, redirects=3, clock=None):
    net = Net(routes)
    kwargs = {"clock": clock} if clock else {}
    table = table or {"a.org": ["93.184.215.14"], "b.org": ["8.8.8.8"]}
    return SafeHttpClient(5, max_bytes, redirects, resolver(table), net, **kwargs), net


def test_connects_to_the_checked_ip_and_tls_uses_the_name():
    c, net = client({"/p": http(200, b"<p>hi</p>", content_type="text/html; charset=utf-8")})
    resp = c.request("GET", "https://a.org/p", PAGE_POLICY)
    assert (resp.status, resp.media_type, resp.body) == (200, "text/html", b"<p>hi</p>")
    assert net.connected == [("93.184.215.14", 443)] and net.tls_names == ["a.org"]
    assert "host: a.org" in net.requests[0][1].lower()
    assert "user-agent: TallerAcademyResearch" in net.requests[0][1]
    assert isinstance(c._client(PAGE_POLICY)._transport, GuardedTransport)
    assert c._client(PAGE_POLICY).trust_env is False
    c.close()


def test_mixed_and_rebinding_answers_are_refused():
    c, net = client({"/": http(200)}, {"a.org": ["93.184.215.14", "10.0.0.1"]})
    with pytest.raises(FetchRefused, match="non-public"):
        c.request("GET", "http://a.org/", PAGE_POLICY)
    assert net.connected == []
    # resolution happens once, at connect, so check and connect use the same answer; a later
    # connection that resolves to a private address (DNS rebinding) is refused
    c, net = client(
        {"/": http(200, connection="close")}, {"a.org": [["93.184.215.14"], ["127.0.0.1"]]}
    )
    assert c.request("GET", "http://a.org/", PAGE_POLICY).status == 200
    with pytest.raises(FetchRefused, match="non-public"):
        c.request("GET", "http://a.org/", PAGE_POLICY)
    assert net.connected == [("93.184.215.14", 80)]


def test_redirects_are_rechecked_capped_and_never_downgrade():
    routes = {
        "/1": http(302, location="/2"),
        "/2": http(301, location="https://b.org/3"),
        "/3": http(200, b"done", content_type="text/plain"),
        "/private": http(302, location="http://127.0.0.1/admin"),
        "/down": http(302, location="http://a.org/3"),
        "/loop": http(307, location="/loop"),
    }
    c, net = client(routes)
    resp = c.request("GET", "https://a.org/1", PAGE_POLICY, follow_redirects=True)
    assert (resp.url, resp.body) == ("https://b.org/3", b"done")
    assert [ip for ip, _ in net.connected][-1] == "8.8.8.8"
    with pytest.raises(FetchRefused, match="non-public"):
        c.request("GET", "http://a.org/private", PAGE_POLICY, follow_redirects=True)
    with pytest.raises(FetchRefused, match="downgrades"):
        c.request("GET", "https://a.org/down", PAGE_POLICY, follow_redirects=True)
    with pytest.raises(FetchFailed, match="too many redirects"):
        c.request("GET", "http://a.org/loop", PAGE_POLICY, follow_redirects=True)
    assert c.request("GET", "http://a.org/1", PAGE_POLICY).status == 302  # not followed


def test_size_caps_count_decompressed_bytes():
    bomb = gzip.compress(b"0" * 2_000_000)
    routes = {
        "/big": http(200, b"x" * 50, content_length="999999"),
        "/bomb": http(200, bomb, content_encoding="gzip", content_type="text/plain"),
        "/stream": [
            http(200, b"", transfer_encoding="chunked").replace(b"content-length: 0\r\n", b"")
        ]
        + [b"400\r\n" + b"y" * 1024 + b"\r\n"] * 20
        + [b"0\r\n\r\n"],
    }
    c, _ = client(routes, max_bytes=4096)
    with pytest.raises(FetchFailed, match="too large"):
        c.request("GET", "http://a.org/big", PAGE_POLICY)
    resp = c.request("GET", "http://a.org/bomb", PAGE_POLICY)
    assert resp.truncated and len(resp.body) == 4096
    resp = c.request("GET", "http://a.org/stream", PAGE_POLICY)
    assert resp.truncated and resp.body == b"y" * 4096


def test_overall_deadline():
    ticks = iter(range(0, 100, 3))  # every clock read advances 3 s; the budget is 5 s
    c, _ = client(
        {"/1": http(302, location="/2"), "/2": http(200, b"ok")}, clock=lambda: next(ticks)
    )
    with pytest.raises(FetchFailed, match="timeout"):
        c.request("GET", "http://a.org/1", PAGE_POLICY, follow_redirects=True)


def test_transport_errors_become_fetch_failed():
    class Down(Net):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            raise httpcore.ConnectError("refused")

    class Slow(Net):
        def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            raise httpcore.ConnectTimeout("slow")

    table = {"a.org": ["93.184.215.14"]}
    for backend, reason in ((Down({}), "connection failed"), (Slow({}), "timeout")):
        c = SafeHttpClient(5, 100, 0, resolver(table), backend)
        with pytest.raises(FetchFailed, match=reason):
            c.request("GET", "http://a.org/", PAGE_POLICY)


class DictCache:
    def __init__(self):
        self.rows = {}

    def get(self, key, ttl):
        return self.rows.get(key)

    def put(self, key, url, text, media_type):
        self.rows[key] = (url, text, media_type)


def test_fetcher_types_charsets_and_cache():
    latin = "café".encode("latin-1")
    routes = {
        "/html": http(200, b"<html>ok</html>"),  # no type: sniffed as HTML
        "/latin": http(200, latin, content_type="text/plain; charset=ISO-8859-1"),
        "/bogus": http(200, b"plain", content_type="text/plain; charset=nope"),
        "/pdf": http(200, b"%PDF", content_type="application/pdf"),
        "/404": http(404, b"gone", content_type="text/html"),
    }
    c, net = client(routes)
    cache = DictCache()
    fetcher = SafeHttpFetcher(c, cache)
    page = fetcher.fetch("http://a.org/html")
    assert (page.media_type, page.text) == ("text/html", "<html>ok</html>")
    assert fetcher.fetch("http://a.org/html") == page and len(net.requests) == 1  # cached
    assert fetcher.fetch("http://a.org/latin").text == "café"
    assert fetcher.fetch("http://a.org/bogus").text == "plain"
    with pytest.raises(FetchFailed, match="unsupported type application/pdf"):
        fetcher.fetch("http://a.org/pdf")
    with pytest.raises(FetchFailed, match="http 404"):
        fetcher.fetch("http://a.org/404")
    assert len(cache.rows) == 3  # failures are never cached


def test_discard_body_drains_a_large_answer_without_failing():
    routes = {"/hook": http(200, b"z" * 50, content_length="999999")}
    c, _ = client(routes, max_bytes=16)
    resp = c.request("POST", "http://a.org/hook", PAGE_POLICY, content=b"{}", discard_body=True)
    assert resp.status == 200 and len(resp.body) <= 16
