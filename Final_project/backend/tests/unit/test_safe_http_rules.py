"""SSRF guard rules (SEARXNG_TOOL_PLAN §3.9, §9): no network."""

import ipaddress

import pytest

from app.domain.errors import FetchRefused
from app.infrastructure.safe_http import (
    PAGE_POLICY,
    callback_policy,
    check_url,
    is_public_ip,
    resolve_public,
)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
        "0.0.0.0", "224.0.0.1",  # noqa: S104 — an address under test, nothing binds
        "255.255.255.255", "::1", "::", "fe80::1", "fd12::1", "ff02::1",
        "::ffff:10.0.0.1", "::ffff:127.0.0.1", "2002:0a00:0001::1", "64:ff9b::a00:1",
        "2001:0:4136:e378:8000:63bf:f5ff:fffe",  # Teredo client 10.0.0.1
    ],
)  # fmt: skip  # noqa: S104 — test data, nothing binds
def test_non_public_addresses_are_refused(address):
    assert not is_public_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    "address", ["93.184.215.14", "8.8.8.8", "2606:4700::1111", "::ffff:8.8.8.8", "64:ff9b::808:808"]
)
def test_public_addresses_pass(address):
    assert is_public_ip(ipaddress.ip_address(address))


@pytest.mark.parametrize(
    ("url", "rule"),
    [
        ("ftp://example.com/x", "scheme"),
        ("file:///etc/passwd", "scheme"),
        ("https://user:pw@example.com/", "credentials"),
        ("https://example.com:8080/", "ports"),
        ("http://localhost/", "public name"),
        ("http://metadata.google.internal/", "public name"),
        ("http://printer.local/", "public name"),
        ("http://[fe80::1%25eth0]/", "valid host"),
        ("https://example.com/" + "a" * 2030, "too long"),
        ("http://", "not valid|valid host"),
    ],
)
def test_url_rules(url, rule):
    with pytest.raises(FetchRefused, match=rule):
        check_url(url, PAGE_POLICY)


def test_url_rules_pass_and_callback_policy():
    assert check_url("https://example.com:443/p?q=1", PAGE_POLICY).host == "example.com"
    assert check_url("http://example.com:80/", PAGE_POLICY).port is None
    with pytest.raises(FetchRefused, match="https"):
        check_url("http://example.com/hook", callback_policy(False))
    local = callback_policy(True)
    assert check_url("http://localhost:8000/hook", local).port == 8000  # local demos only
    with pytest.raises(FetchRefused, match="credentials"):
        check_url("http://u:p@localhost/", local)  # syntax rules still apply


def test_resolve_public_checks_every_address():
    def resolver(answers):
        return lambda host, port: answers

    assert resolve_public("a.org", 443, PAGE_POLICY, resolver(["93.184.215.14"])) == [
        "93.184.215.14"
    ]
    with pytest.raises(FetchRefused, match="non-public"):
        resolve_public("a.org", 443, PAGE_POLICY, resolver(["93.184.215.14", "10.0.0.1"]))
    with pytest.raises(FetchRefused, match="does not resolve"):
        resolve_public("a.org", 443, PAGE_POLICY, resolver([]))

    def broken(host, port):
        raise OSError("nxdomain")

    with pytest.raises(FetchRefused, match="does not resolve"):
        resolve_public("a.org", 443, PAGE_POLICY, broken)
    # IP literals are checked as is, never resolved (catches http://2130706433 after parsing)
    with pytest.raises(FetchRefused, match="non-public"):
        resolve_public("127.0.0.1", 80, PAGE_POLICY, broken)
    assert resolve_public("10.0.0.1", 80, callback_policy(True), broken) == ["10.0.0.1"]
