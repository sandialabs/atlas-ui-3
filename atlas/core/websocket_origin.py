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

    An origin is accepted when it is loopback and the target is loopback too,
    when it names the same host the request was addressed to, or when it
    appears in ``allowed_hosts``.

    Comparison is by hostname; scheme and port are not compared. This is
    deliberate, and it is looser than the browser's own origin definition:
    ``https://atlas.example.com:8443`` is a distinct origin from
    ``https://atlas.example.com`` but is accepted here. Two reasons. First,
    the backend cannot reconstruct the browser-facing origin -- behind a
    TLS-terminating proxy it sees plain HTTP on an internal port, so a strict
    comparison would reject every legitimate upgrade. Second, cookies are not
    isolated by port or (for ``Secure``-less cookies) by scheme, so hostname
    is the granularity at which the ambient credentials this defends actually
    live. The residual risk is a *different* app on another port of the same
    hostname; deployments where that is a real concern should put the exact
    hostnames in ``allowed_hosts`` and front the backend with a proxy that
    does not share a hostname with untrusted applications.

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

    target = extract_host(request_host) if request_host else None

    if hostname in LOOPBACK_HOSTS:
        # Loopback is trusted only when the socket being opened is itself
        # loopback. In production it must not be: a user who visits a
        # malicious page served by some other app on their own machine would
        # otherwise have that page open the production socket, with the
        # browser supplying their cookies and the proxy authenticating it.
        # A caller that passes no request_host (the Agent Portal, which binds
        # loopback anyway) keeps the unconditional local-dev behaviour.
        if target is None or target in LOOPBACK_HOSTS:
            return True
    elif target and hostname == target:
        return True

    return hostname in {host.strip().lower() for host in allowed_hosts if host.strip()}
