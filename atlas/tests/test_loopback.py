"""Tests for the shared loopback-host helper (atlas.core.loopback)."""

import pytest

from atlas.core.loopback import is_localhost_name, is_loopback_host, normalize_host, parse_ip


@pytest.mark.parametrize("host", [
    "localhost",
    "LOCALHOST",
    "localhost.",
    "127.0.0.1",
    "127.0.0.2",
    "127.255.255.254",
    "::1",
    "[::1]",
    "::ffff:127.0.0.1",
    "2130706433",
    "0x7f000001",
    "127.1",
    "0x7f.1",
    "0177.0.0.1",
    "0x7f.0x0.0.1",
])
def test_loopback_hosts(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", [
    "",
    None,
    "example.com",
    "127.example.com",
    "localhost.example.com",
    "evil-localhost",
    "10.0.0.1",
    "0.0.0.0",
    "::",
    "testserver",
    "keycloak.localhost",
    "localhost..",
    "0x",
    "08.0.0.1",
    "127.0.0.256",
    "1.2.3.4.5",
    # Trailing-dot IP literals go to DNS as names.
    "127.0.0.1.",
    "0x7f000001.",
    "127.1.",
])
def test_non_loopback_hosts(host):
    assert not is_loopback_host(host)


@pytest.mark.parametrize("host", ["keycloak.localhost", "A.B.LOCALHOST", "keycloak.localhost."])
def test_localhost_subdomains_only_when_asked(host):
    assert not is_loopback_host(host)
    assert is_loopback_host(host, localhost_subdomains=True)


@pytest.mark.parametrize("host", [".localhost", "a..localhost", "localhost..", "x.localhost.."])
def test_empty_labels_never_match(host):
    assert not is_localhost_name(host, localhost_subdomains=True)
    assert not is_loopback_host(host, localhost_subdomains=True)



def test_normalize_host():
    assert normalize_host(" [::1] ") == "::1"
    assert normalize_host("Example.COM.") == "example.com."
    assert normalize_host(None) == ""


def test_parse_ip():
    assert str(parse_ip("2130706433")) == "127.0.0.1"
    assert parse_ip("example.com") is None
    assert parse_ip(str(2**40)) is None


@pytest.mark.parametrize("host,expected", [
    ("127.1", "127.0.0.1"),
    ("0x7f.1", "127.0.0.1"),
    ("0177.0.0.1", "127.0.0.1"),
    ("10.1", "10.0.0.1"),
    ("1.0xffffff", "1.255.255.255"),
    ("4294967295", "255.255.255.255"),
])
def test_parse_ip_legacy_dotted_forms(host, expected):
    """Matches what inet_aton (and so HTTP clients) would connect to."""
    assert str(parse_ip(host)) == expected


@pytest.mark.parametrize("host", [
    "0x", "08", "1e3", "+1", "1_0", "2_130_706_433", "0b1", "0o177", "-1", "256.1", "0x100.1", "1.0x1000000",
    "4294967296", "\uff11\uff12\uff17.0.0.1",
])
def test_parse_ip_rejects_non_inet_aton_forms(host):
    assert parse_ip(host) is None


def test_parse_ip_rejects_huge_digit_strings_without_raising():
    """int() raises past CPython's 4300-digit limit; the parser must not."""
    assert parse_ip("1" * 5000) is None
    assert parse_ip("127." + "0" * 5000 + ".1") is None
    assert not is_loopback_host("9" * 5000)
