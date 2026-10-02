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
  encodings HTTP clients still accept (``2130706433``, ``0x7f000001``);
- with ``localhost_subdomains=True``, any RFC 6761 ``*.localhost`` name such as
  ``keycloak.localhost``.

Hosts are compared case-insensitively, with IPv6 brackets and one trailing dot
removed. These are checks on the *name*, never on what it resolves to.
"""

import ipaddress
from typing import Iterable, Optional, Union

IPAddress = Union[ipaddress.IPv4Address, ipaddress.IPv6Address]


def normalize_host(host: Optional[str]) -> str:
    """Lowercase ``host`` and drop IPv6 brackets and one trailing dot."""
    host = (host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host.removesuffix(".")


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
    # Integer and other legacy IPv4 encodings that ip_address rejects but
    # resolvers and HTTP clients still accept.
    try:
        packed = int(host, 0)
    except (TypeError, ValueError):
        return None
    if 0 <= packed <= 0xFFFFFFFF:
        return ipaddress.ip_address(packed)
    return None


def is_localhost_name(host: Optional[str], *, localhost_subdomains: bool = False) -> bool:
    """Whether ``host`` is ``localhost`` (or, optionally, a ``*.localhost`` name).

    RFC 6761 section 6.3 reserves every ``*.localhost`` name for loopback;
    local setups use them to give each service its own origin (for example
    ``keycloak.localhost`` behind a local ingress). Empty labels are rejected,
    so ``.localhost`` and ``a..localhost`` do not match.
    """
    host = normalize_host(host)
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
    extra_names: Iterable[str] = (),
) -> bool:
    """Whether ``host`` names the local machine.

    ``localhost_subdomains`` also accepts RFC 6761 ``*.localhost`` names.
    Leave it off where loopback grants extra trust to something a remote
    party controls; those names resolve through the operator's resolver,
    which may point them at a local ingress rather than this process.

    ``extra_names`` adds fixed names, such as the ``testserver`` host
    Starlette's TestClient uses.
    """
    normalized = normalize_host(host)
    if not normalized:
        return False
    if normalized in {normalize_host(name) for name in extra_names}:
        return True
    # The helpers normalize ``host`` themselves; normalizing twice would strip
    # two trailing dots and let ``localhost..`` through.
    if is_localhost_name(host, localhost_subdomains=localhost_subdomains):
        return True
    return is_loopback_ip(parse_ip(host))
