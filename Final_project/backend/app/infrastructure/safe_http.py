"""The SSRF guard (SEARXNG_TOOL_PLAN §3.9): every outbound fetch built from external input
(`read_result` pages, `callback_url` webhooks) goes through it. Owner: SEARXNG; used by
WEBHOOKS.

1. URL rules before any network: scheme per policy, no credentials, ports 80/443, no internal
   host names, ≤ 2,048 characters.
2. IP rules on EVERY resolved address: `is_global`, not multicast, and IPv4 addresses embedded
   in IPv6 (mapped, 6to4, Teredo, NAT64) must pass too. Rules run on resolved addresses, never
   on the string, so `http://2130706433` style tricks are caught.
3. The connection goes to the CHECKED IP: GuardedBackend resolves, checks and connects in one
   step (no DNS rebinding between check and connect); httpcore still sends Host and SNI with
   the name, so TLS verification uses the name.
4. Redirects are followed manually (pages only, ≤ FETCH_MAX_REDIRECTS), each hop re-checked;
   https → http downgrades refused. Size and time are capped, counting DECOMPRESSED bytes.

Only public httpx/httpcore APIs are used (review decision, BACKEND_PLAN §24.2)."""

import hashlib
import ipaddress
import logging
import socket
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urljoin

import httpcore
import httpx

from app.domain.errors import FetchFailed, FetchRefused
from app.domain.ports import FetchedPage

logger = logging.getLogger("app.safe_http")
IP = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str, int], list[str]]
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".arpa")
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_REDIRECTS = {301, 302, 303, 307, 308}
USER_AGENT = "TallerAcademyResearch/1.0 (+https://github.com/RodrigoDamasio/TA_FinalProject)"
PAGE_TYPES = {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}


@dataclass(frozen=True)
class UrlPolicy:
    schemes: frozenset[str]
    ports: frozenset[int] = frozenset({80, 443})
    allow_private: bool = False  # tests and local demos only (CALLBACK_ALLOW_PRIVATE)


PAGE_POLICY = UrlPolicy(frozenset({"http", "https"}))


def callback_policy(allow_private: bool) -> UrlPolicy:
    if allow_private:
        return UrlPolicy(frozenset({"http", "https"}), allow_private=True)
    return UrlPolicy(frozenset({"https"}))


# ---- rules ----------------------------------------------------------------------------------


def check_url(url: str, policy: UrlPolicy) -> httpx.URL:
    """Syntax rules (no network). Raises FetchRefused naming the rule, never an address."""
    if len(url) > 2048:
        raise FetchRefused("the URL is too long")
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, ValueError, TypeError) as err:
        raise FetchRefused("the URL is not valid") from err
    if parsed.scheme not in policy.schemes:
        raise FetchRefused(f"the scheme must be {' or '.join(sorted(policy.schemes))}")
    if parsed.userinfo:
        raise FetchRefused("credentials in the URL are not allowed")
    host = parsed.host
    if not host or len(host) > 253 or "%" in host:
        raise FetchRefused("the URL has no valid host")
    if policy.allow_private:
        return parsed
    if host == "localhost" or host.endswith(_INTERNAL_SUFFIXES):
        raise FetchRefused("the host is not a public name")
    if parsed.port is not None and parsed.port not in policy.ports:
        raise FetchRefused("only ports 80 and 443 are allowed")
    return parsed


def _embedded_v4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    found = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found += list(ip.teredo)
    if ip in _NAT64:
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def is_public_ip(ip: IP) -> bool:
    """Global and not multicast; an IPv4 address embedded in IPv6 must pass too."""
    if not ip.is_global or ip.is_multicast:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        return all(is_public_ip(v4) for v4 in _embedded_v4(ip))
    return True


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def resolve_public(
    host: str, port: int, policy: UrlPolicy, resolver: Resolver = system_resolver
) -> list[str]:
    """Every address the host resolves to must be public (one bad address refuses it)."""
    try:
        addresses = [host] if _is_ip(host) else resolver(host, port)
    except OSError as err:
        raise FetchRefused("the host does not resolve") from err
    if not addresses:
        raise FetchRefused("the host does not resolve")
    if not policy.allow_private:
        for address in addresses:
            if not is_public_ip(ipaddress.ip_address(address.split("%")[0])):
                raise FetchRefused("the host resolves to a non-public address")
    return addresses


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


# ---- transport: connect to the checked IP -------------------------------------------------------


class GuardedBackend(httpcore.NetworkBackend):
    def __init__(
        self,
        policy: UrlPolicy,
        resolver: Resolver = system_resolver,
        inner: httpcore.NetworkBackend | None = None,
    ) -> None:
        self._policy, self._resolver = policy, resolver
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> httpcore.NetworkStream:
        address = resolve_public(host.strip("[]"), port, self._policy, self._resolver)[0]
        return self._inner.connect_tcp(address, port, timeout, local_address, socket_options)

    def connect_unix_socket(self, path: str, timeout: float | None = None,
                            socket_options: Any = None) -> httpcore.NetworkStream:  # fmt: skip
        raise FetchRefused("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class _Body(httpx.SyncByteStream):
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def __iter__(self) -> Iterator[bytes]:
        yield from self._stream

    def close(self) -> None:
        close = getattr(self._stream, "close", None)
        if close is not None:
            close()


class GuardedTransport(httpx.BaseTransport):
    """An httpx transport over an httpcore pool whose network backend is GuardedBackend."""

    def __init__(self, backend: httpcore.NetworkBackend) -> None:
        self._pool = httpcore.ConnectionPool(network_backend=backend, retries=0, http2=False)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            resp = self._pool.handle_request(req)
        except httpcore.TimeoutException as err:
            raise httpx.TimeoutException(str(err), request=request) from err
        except httpcore.NetworkError as err:
            raise httpx.TransportError(str(err)) from err
        except httpcore.ProtocolError as err:
            raise httpx.TransportError(str(err)) from err
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_Body(resp.stream),
            extensions=resp.extensions,
        )

    def close(self) -> None:
        self._pool.close()


# ---- the client ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class SafeResponse:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    media_type: str = ""
    body: bytes = b""
    truncated: bool = False


class SafeHttpClient:
    def __init__(
        self,
        timeout_s: float,
        max_bytes: int,
        max_redirects: int,
        resolver: Resolver = system_resolver,
        backend: httpcore.NetworkBackend | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout, self._max_bytes, self._max_redirects = timeout_s, max_bytes, max_redirects
        self._resolver, self._backend, self._clock = resolver, backend, clock
        self._clients: dict[UrlPolicy, httpx.Client] = {}

    def _client(self, policy: UrlPolicy) -> httpx.Client:
        if policy not in self._clients:
            backend = GuardedBackend(policy, self._resolver, self._backend)
            self._clients[policy] = httpx.Client(
                transport=GuardedTransport(backend), trust_env=False, follow_redirects=False
            )
        return self._clients[policy]

    def request(
        self,
        method: Literal["GET", "POST"],
        url: str,
        policy: UrlPolicy,
        *,
        content: bytes | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool = False,
        discard_body: bool = False,
    ) -> SafeResponse:
        """Raises FetchRefused (a rule) or FetchFailed (timeout, too many redirects, …).
        `discard_body` (webhooks): a large declared body is not an error; at most max_bytes
        are read and the rest is dropped."""
        deadline = self._clock() + self._timeout
        current, hops = url, 0
        sent = {"user-agent": USER_AGENT, "accept-encoding": "gzip, deflate", **(headers or {})}
        while True:
            parsed = check_url(current, policy)
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise FetchFailed("timeout")
            timeout = httpx.Timeout(remaining, connect=min(3.0, remaining))
            try:
                with self._client(policy).stream(
                    method, parsed, content=content, headers=sent, timeout=timeout
                ) as resp:
                    location = resp.headers.get("location")
                    if follow_redirects and resp.status_code in _REDIRECTS and location:
                        nxt = urljoin(str(parsed), location)
                        if parsed.scheme == "https" and nxt.startswith("http:"):
                            raise FetchRefused("a redirect downgrades https to http")
                        hops += 1
                        if hops > self._max_redirects:
                            raise FetchFailed("too many redirects")
                        if resp.status_code in (301, 302, 303):
                            method, content = "GET", None
                        current = nxt
                        continue
                    return self._read(resp, deadline, discard_body)
            except httpx.TimeoutException as err:
                raise FetchFailed("timeout") from err
            except httpx.TransportError as err:
                raise FetchFailed("connection failed") from err

    def _read(self, resp: httpx.Response, deadline: float, discard: bool = False) -> SafeResponse:
        declared = resp.headers.get("content-length", "")
        if not discard and declared.isdigit() and int(declared) > self._max_bytes:
            raise FetchFailed("too large")
        body = bytearray()
        truncated = False
        for chunk in resp.iter_bytes():  # decompressed: a gzip bomb stops at the cap
            body += chunk
            if len(body) > self._max_bytes:
                del body[self._max_bytes :]
                truncated = True
                break
            if self._clock() > deadline:
                raise FetchFailed("timeout")
        media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        return SafeResponse(
            str(resp.url), resp.status_code, {k.lower(): v for k, v in resp.headers.items()},
            media_type, bytes(body), truncated,
        )  # fmt: skip

    def close(self) -> None:
        for client in self._clients.values():
            client.close()


# ---- pages ------------------------------------------------------------------------------------


class SafeHttpFetcher:
    """PageFetcher: GET through the guard, redirects on, allowed text types only, decoded with
    the declared charset (else detected), cached 24 h. Failures are never cached."""

    def __init__(
        self, client: SafeHttpClient, cache: Any | None = None, ttl_s: float = 86400
    ) -> None:
        self._client, self._cache, self._ttl = client, cache, ttl_s

    def fetch(self, url: str) -> FetchedPage:
        key = hashlib.sha256(url.encode()).hexdigest()
        if self._cache is not None:
            hit = self._cache.get(key, self._ttl)
            if hit is not None:
                final, text, media_type = hit
                return FetchedPage(final, 200, media_type, text)
        resp = self._client.request("GET", url, PAGE_POLICY, follow_redirects=True)
        if resp.status >= 400:
            raise FetchFailed(f"http {resp.status}")
        media_type = resp.media_type or ("text/html" if resp.body.lstrip()[:1] == b"<" else "")
        if media_type not in PAGE_TYPES:
            raise FetchFailed(f"unsupported type {media_type or 'unknown'}")
        text = _decode(resp.body, resp.headers.get("content-type", ""))
        if self._cache is not None:
            self._cache.put(key, resp.url, text, media_type)
        logger.info("fetch host=%s bytes=%d truncated=%s", httpx.URL(resp.url).host,
                    len(resp.body), resp.truncated)  # fmt: skip
        return FetchedPage(resp.url, resp.status, media_type, text)


def _decode(body: bytes, content_type: str) -> str:
    charset = ""
    for part in content_type.split(";")[1:]:
        name, _, value = part.strip().partition("=")
        if name.lower() == "charset":
            charset = value.strip('"').strip()
    if charset:
        try:
            return body.decode(charset, errors="replace")
        except LookupError:
            pass
    try:
        from charset_normalizer import from_bytes  # a trafilatura dependency

        best = from_bytes(body).best()
        if best is not None:
            return str(best)
    except ImportError:  # pragma: no cover — always installed with trafilatura
        pass
    return body.decode("utf-8", errors="replace")
