"""Resolve the configured Help display mode and its documentation URL (issue #1107).

``HELP_DISPLAY_MODE`` picks where the Help page gets its content:

``local`` (default)
    The Markdown that ships with Atlas (``atlas/config/help.md``) or the
    operator's override (``config/help.md``). The pre-#1107 behavior, and the
    default, so an existing deployment is unchanged.

``remote_markdown``
    Markdown fetched server-side from ``HELP_CONTENT_URL`` and rendered in the
    same Help page, with the images it references resolved against that
    document.

``external_link``
    The Help button opens ``HELP_CONTENT_URL`` in a new browser tab. Nothing
    is fetched and nothing is rendered in Atlas.

A mode that names remote content but has no usable URL is a configuration
error. It is *reported* -- the message travels to the API, the Admin UI and
the logs -- and the effective mode falls back to ``local``, so the operator is
never left with a Help button that goes nowhere and Atlas still starts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from atlas.core.loopback import is_loopback_host

logger = logging.getLogger(__name__)

HELP_MODE_LOCAL = "local"
HELP_MODE_REMOTE_MARKDOWN = "remote_markdown"
HELP_MODE_EXTERNAL_LINK = "external_link"

VALID_HELP_MODES = (HELP_MODE_LOCAL, HELP_MODE_REMOTE_MARKDOWN, HELP_MODE_EXTERNAL_LINK)


@dataclass(frozen=True)
class HelpConfig:
    """The resolved Help configuration for one request.

    ``mode`` is what Atlas actually does; ``requested_mode`` is what the
    operator asked for. They differ only when the request cannot be honored,
    in which case ``error`` says why.
    """

    mode: str
    requested_mode: str
    content_url: str = ""
    error: Optional[str] = None
    allowed_hosts: tuple = ()
    timeout_seconds: float = 10.0
    cache_ttl_seconds: int = 300
    max_bytes: int = 1_048_576
    max_image_bytes: int = 5_242_880

    @property
    def is_local(self) -> bool:
        return self.mode == HELP_MODE_LOCAL

    @property
    def is_remote_markdown(self) -> bool:
        return self.mode == HELP_MODE_REMOTE_MARKDOWN

    @property
    def is_external_link(self) -> bool:
        return self.mode == HELP_MODE_EXTERNAL_LINK

    @property
    def has_external_source(self) -> bool:
        """True when a documentation URL is configured, whatever the mode."""
        return bool(self.content_url)

    def as_public_dict(self, include_error: bool = True) -> dict:
        """The subset of the configuration the browser is allowed to see.

        The URL is included because the browser has to open it (external
        link mode) and because the Admin UI has to show which document is
        configured. The error message is included so a misconfiguration is
        visible in the UI rather than only in the server log -- but it names
        the settings an operator has to change, so a caller that is not
        serving an administrator passes ``include_error=False``.
        """
        return {
            "mode": self.mode,
            "requested_mode": self.requested_mode,
            "content_url": self.content_url,
            "error": self.error if include_error else None,
        }


def _is_loopback_host(hostname: str) -> bool:
    """True for hosts that only resolve to the local machine.

    The shared definition (see ``atlas.core.loopback``) is the one this
    codebase uses, so a host that counts as loopback here counts as
    loopback everywhere else.
    """
    return is_loopback_host(hostname)


def validate_help_url(raw_url: str) -> tuple[Optional[str], Optional[str]]:
    """Validate a documentation URL.

    Returns ``(url, None)`` when the URL is usable and ``(None, message)``
    when it is not. The message names the setting and says what is wrong, so
    it can be shown to an operator as-is.
    """
    url = (raw_url or "").strip()
    if not url:
        return None, "HELP_CONTENT_URL is empty"

    try:
        parsed = urlparse(url)
    except ValueError as exc:  # pragma: no cover - urlparse rarely raises
        return None, f"HELP_CONTENT_URL is not a valid URL ({exc})"

    if parsed.scheme not in ("http", "https"):
        return None, (
            f"HELP_CONTENT_URL must be an absolute http(s) URL (got '{parsed.scheme or 'no'}://...')"
        )
    if not parsed.hostname:
        return None, "HELP_CONTENT_URL must include a host"
    if parsed.username or parsed.password:
        return None, "HELP_CONTENT_URL must not embed credentials in the URL"
    if parsed.scheme == "http" and not _is_loopback_host(parsed.hostname):
        return None, (
            "HELP_CONTENT_URL must use https "
            "(http is accepted only for loopback hosts such as localhost)"
        )
    return url, None


def parse_allowed_hosts(raw: str) -> tuple:
    """Parse ``HELP_ALLOWED_HOSTS`` into ``(host, port)`` pairs.

    Entries are ``host`` or ``host:port``. A scheme, a path, or a wildcard is
    rejected: the list widens which hosts may serve Help images, so it has to
    name hosts exactly.
    """
    hosts = []
    for item in (raw or "").split(","):
        entry = item.strip().lower()
        if not entry:
            continue
        if "://" in entry or "/" in entry or "*" in entry:
            logger.warning(
                "Ignoring HELP_ALLOWED_HOSTS entry '%s': expected host or host:port",
                entry,
            )
            continue
        host, _, port = entry.rpartition(":")
        if not host:
            host, port = entry, ""
        try:
            port_number = int(port) if port else None
        except ValueError:
            logger.warning("Ignoring HELP_ALLOWED_HOSTS entry '%s': invalid port", entry)
            continue
        hosts.append((host, port_number))
    return tuple(hosts)


def resolve_help_config(app_settings) -> HelpConfig:
    """Work out how the Help page should behave for this deployment.

    Never raises: a bad configuration degrades to the local Help content and
    reports why through ``error``.
    """
    requested = (getattr(app_settings, "help_display_mode", "") or "").strip().lower()
    if not requested:
        requested = HELP_MODE_LOCAL

    timeout = float(getattr(app_settings, "help_remote_timeout_seconds", 10.0) or 10.0)
    ttl = int(getattr(app_settings, "help_remote_cache_ttl_seconds", 300) or 0)
    max_bytes = int(getattr(app_settings, "help_remote_max_bytes", 1_048_576) or 1_048_576)
    max_image_bytes = int(
        getattr(app_settings, "help_remote_max_image_bytes", 5_242_880) or 5_242_880
    )
    allowed_hosts = parse_allowed_hosts(getattr(app_settings, "help_allowed_hosts", ""))

    def build(mode: str, content_url: str = "", error: Optional[str] = None) -> HelpConfig:
        return HelpConfig(
            mode=mode,
            requested_mode=requested,
            content_url=content_url,
            error=error,
            allowed_hosts=allowed_hosts,
            timeout_seconds=timeout,
            cache_ttl_seconds=ttl,
            max_bytes=max_bytes,
            max_image_bytes=max_image_bytes,
        )

    if requested not in VALID_HELP_MODES:
        return build(
            HELP_MODE_LOCAL,
            error=(
                f"HELP_DISPLAY_MODE='{requested}' is not a valid help display mode "
                f"(expected one of: {', '.join(VALID_HELP_MODES)}). "
                "Falling back to the local Help content."
            ),
        )

    if requested == HELP_MODE_LOCAL:
        return build(HELP_MODE_LOCAL)

    raw_url = getattr(app_settings, "help_content_url", "") or ""
    url, error = validate_help_url(raw_url)
    if error:
        return build(
            HELP_MODE_LOCAL,
            error=(
                f"HELP_DISPLAY_MODE='{requested}' requires a usable HELP_CONTENT_URL: {error}. "
                "Falling back to the local Help content."
            ),
        )

    return build(requested, content_url=url)


# Configuration problems are reported once per distinct message: the resolver
# runs on every request, and a misconfigured deployment would otherwise repeat
# the same error on every poll.
_reported_problems: set = set()


def report_help_config_problem(config: HelpConfig) -> None:
    """Log a Help configuration error once, loudly."""
    if not config.error:
        return
    if config.error in _reported_problems:
        return
    _reported_problems.add(config.error)
    logger.error("Help configuration error: %s", config.error)


def reset_reported_problems() -> None:
    """Forget which configuration problems have been logged (used by tests)."""
    _reported_problems.clear()
