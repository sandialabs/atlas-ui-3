"""Shared loopback-host checks.

Several features relax a rule when a URL points at the local machine: an
``http://`` OIDC issuer is accepted on loopback, the MCP OAuth flow tolerates
plaintext endpoints on loopback, and the Wormhole subtoken warning stays quiet
for loopback servers. They used to carry their own host lists, which drifted
apart. This module is the one definition; callers choose how wide it is.

What counts as loopback here:

- the name ``localhost``;
- any loopback IP literal: ``127.0.0.0/8``, ``::1`` and IPv4-mapped forms such
  as ``::ffff:127.0.0.1``, including the legacy integer and hex IPv4
  encodings HTTP clients still accept (``2130706433``, ``0x7f000001``,
  ``127.1``, ``0x7f.1``, ``0177.0.0.1``);
- with ``localhost_subdomains=True``, any RFC 6761 ``*.localhost`` name such as
  ``keycloak.localhost``.

Hosts are compared case-insensitively with IPv6 brackets removed. One trailing
(root) dot is allowed on *names* (``localhost.``) but not on IP literals:
resolvers send ``127.0.0.1.`` to DNS as a name, so it is not loopback. These
are checks on the *name*, never on what it resolves to.
"""

import ipaddress
import re
from typing import Optional, Union

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]

# One part of a legacy IPv4 literal: hex, octal (leading zero) or decimal.
_LEGACY_IPV4_PART = re.compile(r"0[xX][0-9a-fA-F]+|0[0-7]*|[1-9][0-9]*", re.ASCII)

# Longest legacy IPv4 part worth converting. Real values fit in 12 characters
# (037777777777); the cap also keeps attacker-sized digit strings away from
# int(), which raises ValueError past CPython's 4300-digit limit.
_MAX_LEGACY_IPV4_PART = 32


def normalize_host(host: Optional[str]) -> str:
    """Lowercase ``host`` and drop surrounding whitespace and IPv6 brackets."""
    host = (host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def _name_form(host: Optional[str]) -> str:
    """Normalized ``host`` with one trailing root dot removed, for name matching."""
    return normalize_host(host).removesuffix(".")


def parse_ip(host: Optional[str]) -> Optional[IPAddress]:
    """Parse a host as an IP address, or None when it is a name.

    ``ipaddress`` is what normalizes the encodings an allowlist written by
    hand would miss: ``0x7f000001``, ``2130706433``, ``::ffff:127.0.0.1`` and
    ``0.0.0.0`` all resolve to addresses.
    """
    host = normalize_host(host)
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    return _parse_legacy_ipv4(host)


def _parse_legacy_ipv4(host: str) -> Optional[ipaddress.IPv4Address]:
    """Parse the BSD ``inet_aton`` IPv4 forms that ``ipaddress`` rejects.

    Resolvers and HTTP clients still accept one to four dot-separated parts,
    each decimal, ``0x`` hex or leading-zero octal, with the last part filling
    the remaining bytes: ``2130706433``, ``0x7f000001``, ``127.1``,
    ``0x7f.1`` and ``0177.0.0.1`` are all ``127.0.0.1``.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    values = []
    for part in parts:
        if len(part) > _MAX_LEGACY_IPV4_PART or not _LEGACY_IPV4_PART.fullmatch(part):
            return None
        if part[:2].lower() == "0x":
            values.append(int(part[2:], 16))
        elif len(part) > 1 and part[0] == "0":
            values.append(int(part, 8))
        else:
            values.append(int(part, 10))
    *head, last = values
    if any(v > 0xFF for v in head) or last >= 1 << (8 * (4 - len(head))):
        return None
    packed = 0
    for v in head:
        packed = (packed << 8) | v
    packed = (packed << (8 * (4 - len(head)))) | last
    return ipaddress.IPv4Address(packed)


def is_localhost_name(host: Optional[str], *, localhost_subdomains: bool = False) -> bool:
    """Whether ``host`` is ``localhost`` (or, optionally, a ``*.localhost`` name).

    RFC 6761 section 6.3 reserves every ``*.localhost`` name for loopback;
    local setups use them to give each service its own origin (for example
    ``keycloak.localhost`` behind a local ingress). Empty labels are rejected,
    so ``.localhost`` and ``a..localhost`` do not match. One trailing root
    dot is allowed (``localhost.``).
    """
    host = _name_form(host)
    if host == "localhost":
        return True
    if not localhost_subdomains:
        return False
    labels = host.split(".")
    return len(labels) > 1 and labels[-1] == "localhost" and all(labels)


def is_loopback_ip(address: Optional[IPAddress]) -> bool:
    """Whether an address is loopback, unwrapping IPv4-mapped IPv6 first."""
    if address is None:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback


def is_loopback_host(
    host: Optional[str],
    *,
    localhost_subdomains: bool = False,
) -> bool:
    """Whether ``host`` names the local machine.

    ``localhost_subdomains`` also accepts RFC 6761 ``*.localhost`` names.
    Leave it off where loopback grants extra trust to something a remote
    party controls; those names resolve through the operator's resolver,
    which may point them at a local ingress rather than this process.
    """
    name = _name_form(host)
    if not name:
        return False
    if is_localhost_name(host, localhost_subdomains=localhost_subdomains):
        return True
    return is_loopback_ip(parse_ip(host))
