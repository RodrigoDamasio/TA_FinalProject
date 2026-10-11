"""`callback_url` rules (WEBHOOKS_PLAN §3.5), checked before `RunService.submit` queues
anything. Syntax here; the host's addresses through the SSRF guard (`safe_http.check_url`,
injected as `resolve`), which runs again at every delivery. Reasons name the rule broken,
never the URL or its resolved addresses. Owner: WEBHOOKS."""

from collections.abc import Callable
from urllib.parse import urlsplit

from app.domain.errors import FetchRefused

MAX_URL_CHARS = 2048


def check_callback_url(
    url: str, *, allow_private: bool, resolve: Callable[[str], None] = lambda url: None
) -> None:
    """Raises FetchRefused(reason). `allow_private` (CALLBACK_ALLOW_PRIVATE, tests and the
    local receiver only) allows http, any port and private or loopback hosts."""
    if len(url) > MAX_URL_CHARS:
        raise FetchRefused(f"the URL is longer than {MAX_URL_CHARS} characters")
    parts = urlsplit(url)
    schemes = ("http", "https") if allow_private else ("https",)
    if parts.scheme not in schemes:
        raise FetchRefused("the scheme must be " + " or ".join(schemes))
    if "@" in parts.netloc:
        raise FetchRefused("credentials in the URL are not allowed")
    if parts.fragment or url.endswith("#"):
        raise FetchRefused("a fragment (#…) is not allowed")
    if not parts.hostname:
        raise FetchRefused("the URL has no host")
    try:
        port = parts.port
    except ValueError as err:
        raise FetchRefused("the port is not valid") from err
    if allow_private:
        return
    if port not in (None, 443):
        raise FetchRefused("only the default HTTPS port (443) is allowed")
    resolve(url)  # every resolved address must be public


def callback_url_check(
    allow_private: bool, resolve: Callable[[str], None]
) -> Callable[[str], None]:
    """The `url_check` callable `RunService` takes (v1.1 §24.3)."""

    def check(url: str) -> None:
        check_callback_url(url, allow_private=allow_private, resolve=resolve)

    return check
