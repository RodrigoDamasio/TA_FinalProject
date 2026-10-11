"""Step 3 STUB of the SSRF guard: URL syntax + every resolved address must be public.
SEARXNG_TOOL_PLAN §3.9 replaces it (connect to the checked IP, redirects, caps, IPv6 rules)
and adds SafeHttpClient / SafeHttpFetcher. Owner: SEARXNG (used by WEBHOOKS)."""

import ipaddress
import socket
from urllib.parse import urlsplit

from app.domain.errors import FetchFailed, FetchRefused
from app.domain.ports import FetchedPage


def check_url(url: str, *, schemes: frozenset[str], allow_private: bool = False) -> None:
    """Raises FetchRefused naming the rule broken (never the resolved address)."""
    if len(url) > 2048:
        raise FetchRefused("the URL is too long")
    parts = urlsplit(url)
    if parts.scheme not in schemes:
        raise FetchRefused(f"the scheme must be {' or '.join(sorted(schemes))}")
    if parts.username or parts.password:
        raise FetchRefused("credentials in the URL are not allowed")
    host = parts.hostname or ""
    if not host:
        raise FetchRefused("the URL has no host")
    if allow_private:  # tests and local demos only (CALLBACK_ALLOW_PRIVATE)
        return
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise FetchRefused("the host is not a public name")
    if parts.port not in (None, 80, 443):
        raise FetchRefused("only ports 80 and 443 are allowed")
    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as err:
        raise FetchRefused("the host does not resolve") from err
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise FetchRefused("the host resolves to a non-public address")


class StubPageFetcher:
    """PageFetcher until SEARXNG implements SafeHttpFetcher: always fails politely."""

    def fetch(self, url: str) -> FetchedPage:
        raise FetchFailed("page reading is not implemented yet")
