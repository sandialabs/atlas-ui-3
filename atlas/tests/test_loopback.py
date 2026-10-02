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


def test_extra_names():
    assert is_loopback_host("TestServer", extra_names=("testserver",))
    assert not is_loopback_host("testserver")


def test_normalize_host():
    assert normalize_host(" [::1] ") == "::1"
    assert normalize_host("Example.COM.") == "example.com"
    assert normalize_host(None) == ""


def test_parse_ip():
    assert str(parse_ip("2130706433")) == "127.0.0.1"
    assert parse_ip("example.com") is None
    assert parse_ip(str(2**40)) is None
