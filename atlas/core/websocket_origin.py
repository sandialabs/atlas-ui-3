"""Origin validation for WebSocket upgrades.

A WebSocket upgrade is not subject to a CORS preflight, so the same-origin
policy does not protect a WebSocket endpoint the way it protects ``fetch``.
Any page the user visits can open a socket to another host, and the browser
attaches that host's cookies to the handshake. Behind an authenticating
reverse proxy the upgrade is then authenticated on the victim's behalf and the
attacker's page holds a live, fully privileged session -- cross-site WebSocket
hijacking. The only defence is for the endpoint to inspect ``Origin`` itself.

This module holds the shared primitives. Endpoint-specific policy (which
origins are allowed, and what to do when the header is absent) stays with the
endpoint, because the two WebSocket surfaces in this app answer those
questions differently.
"""

from __future__ import annotations

from typing import Iterable, Optional
from urllib.parse import urlsplit

__all__ = [
    "LOOPBACK_HOSTS",
    "extract_host",
    "origin_is_allowed",
    "parse_allowed_hosts",
]

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def parse_allowed_hosts(raw: Optional[str]) -> frozenset[str]:
    """Split a comma-separated hostname allowlist into normalized entries.

    Blank entries and surrounding whitespace are discarded so that a value
    like ``"a.example.com, , b.example.com "`` behaves as intended.
    """
    if not raw:
        return frozenset()
    return frozenset(host.strip().lower() for host in raw.split(",") if host.strip())


def extract_host(value: Optional[str]) -> Optional[str]:
    """Return the lowercased hostname from an Origin or Host header value.

    Handles both forms the two headers take: ``Origin`` carries a scheme
    (``https://host:port``) while ``Host`` does not (``host:port``, or
    ``[::1]:8000`` for IPv6). Returns ``None`` if no hostname can be read.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    # Host headers have no scheme; prefixing "//" makes urlsplit treat the
    # whole value as an authority instead of a path, which also gets the
    # bracketed-IPv6 and port-stripping cases right for free.
    if "//" not in candidate:
        candidate = f"//{candidate}"
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return None
    return hostname.lower() if hostname else None


def origin_is_allowed(
    origin: Optional[str],
    allowed_hosts: Iterable[str] = (),
    *,
    request_host: Optional[str] = None,
) -> bool:
    """Return True if ``origin`` may open a socket to this server.

    An origin is accepted when it is loopback, when it names the same host the
    request was addressed to, or when it appears in ``allowed_hosts``. Ports
    are ignored throughout -- a same-host page on another port is same-site
    for this purpose, and requiring a port match would break every deployment
    that terminates TLS on one port and serves on another.

    A missing or malformed ``Origin`` returns False. Callers that want to
    admit non-browser clients must special-case that before calling; the
    decision is theirs, not this function's.

    On trusting ``Host``: a client can of course send any ``Host`` it likes,
    and so make its own ``Origin`` match. That does not weaken anything here.
    The attack this guards against needs a *browser* to attach the victim's
    cookies, and a browser sets ``Host`` from the URL being connected to --
    script on the attacker's page cannot override it. An attacker willing to
    forge both headers is simply an unauthenticated client talking to the
    server directly, which is what the proxy secret and the auth header are
    for.

    Args:
        origin: Raw ``Origin`` header value.
        allowed_hosts: Extra hostnames to accept, already-normalized or not.
        request_host: Raw ``Host`` header value, enabling the same-origin
            comparison. Omit it to check only loopback and the allowlist.
    """
    if not origin:
        return False

    try:
        parsed = urlsplit(origin.strip())
    except ValueError:
        return False

    # "null" and other opaque origins arrive as a value with no scheme, as do
    # sandboxed iframes and file:// pages. None of them are same-origin.
    if parsed.scheme not in ("http", "https"):
        return False

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return False

    if hostname in LOOPBACK_HOSTS:
        return True

    if request_host:
        target = extract_host(request_host)
        if target and hostname == target:
            return True

    return hostname in {host.strip().lower() for host in allowed_hosts if host.strip()}
