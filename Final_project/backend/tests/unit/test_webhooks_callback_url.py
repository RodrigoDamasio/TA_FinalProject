"""`callback_url` rules, both modes (WEBHOOKS_PLAN §3.5, §9 unit). The SSRF guard runs with a
fake resolver: 0 network."""

import pytest

from app.application.outbox.callback_url import callback_url_check, check_callback_url
from app.domain.errors import FetchRefused


class Resolver:
    """Stands in for safe_http.check_url: refuses the hosts it is told are private."""

    def __init__(self, private: set[str] = frozenset()) -> None:
        self.private, self.calls = set(private), []

    def __call__(self, url: str) -> None:
        self.calls.append(url)
        if any(f"//{h}" in url for h in self.private):
            raise FetchRefused("the host resolves to a non-public address")


def refused(url: str, allow_private: bool = False, resolve=None) -> str:
    with pytest.raises(FetchRefused) as info:
        check_callback_url(url, allow_private=allow_private, resolve=resolve or Resolver())
    return info.value.reason


@pytest.mark.parametrize(
    "url",
    ["https://hooks.example.com/h", "https://hooks.example.com:443/a/b?t=1", "https://8.8.8.8/x"],
)
def test_production_accepts_public_https(url):
    resolve = Resolver()
    check_callback_url(url, allow_private=False, resolve=resolve)
    assert resolve.calls == [url]  # the address check always runs in production


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://hooks.example.com/h", "scheme must be https"),
        ("ftp://hooks.example.com/h", "scheme must be https"),
        ("https://hooks.example.com:8443/h", "443"),
        ("https://hooks.example.com:80/h", "443"),
        ("https://user:pass@hooks.example.com/h", "credentials"),
        ("https://token@hooks.example.com/h", "credentials"),
        ("https://hooks.example.com/h#frag", "fragment"),
        ("https://hooks.example.com/h#", "fragment"),
        ("https:///path-only", "no host"),
        ("https://hooks.example.com:99999/h", "port is not valid"),
        ("https://hooks.example.com/" + "a" * 2048, "longer than 2048"),
    ],
)
def test_production_rules(url, reason):
    assert reason in refused(url)


def test_private_address_is_refused_without_naming_it():
    reason = refused("https://internal.example/h", resolve=Resolver({"internal.example"}))
    assert reason == "the host resolves to a non-public address"


def test_allow_private_mode():
    resolve = Resolver({"localhost"})
    for url in ("http://localhost:8001/hook", "https://127.0.0.1:9000/h", "http://10.0.0.5/h"):
        check_callback_url(url, allow_private=True, resolve=resolve)
    assert resolve.calls == []  # host and port rules are skipped
    assert "credentials" in refused("http://u:p@localhost:8001/h", allow_private=True)
    assert "fragment" in refused("http://localhost:8001/h#x", allow_private=True)
    assert "http or https" in refused("ftp://localhost/h", allow_private=True)


def test_url_check_factory_plugs_into_run_service():
    resolve = Resolver({"bad.example"})
    check = callback_url_check(False, resolve)
    check("https://good.example/h")
    with pytest.raises(FetchRefused):
        check("https://bad.example/h")
