"""Refuse to start with development-only MCP servers enabled in production.

Several MCP servers shipped as example configurations hand an authenticated
user the host itself: ``file_viewer`` and ``filesystem`` read arbitrary paths
and return the bytes, ``code-executor`` runs supplied code, ``transfer``
fetches arbitrary URLs. They exist for local development and demos, and they
are fine there.

The failure mode this guards is not someone deliberately enabling one in
production. It is an operator copying an example config into a shared
deployment, where a tool intended for a laptop becomes arbitrary file read and
SSRF for every authenticated user. Nothing in the system objected to that.

The guard follows the pattern already used for the Agent Portal
(``validate_agent_portal_dev_only``): refuse to boot rather than warn, because
a warning in a startup log is not a control.

A server is treated as development-only when its config says ``"dev_only":
true``, or when its name is in :data:`DEV_ONLY_BY_DEFAULT` and it has not
explicitly opted out with ``"dev_only": false``. The name-based default
matters: an operator copying ``mcp-file_viewer.json`` will not add a marker
they have never heard of, and that is precisely the case worth catching.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping

logger = logging.getLogger(__name__)

__all__ = [
    "DEV_ONLY_BY_DEFAULT",
    "MCPDevOnlyServerError",
    "enforce_dev_only_mcp_servers",
    "find_enabled_dev_only_servers",
]

# Servers that grant host access and are treated as development-only unless a
# config explicitly sets "dev_only": false. Keys are matched case-insensitively
# with '-' and '_' treated as equivalent, since the example configs are
# inconsistent about which they use.
DEV_ONLY_BY_DEFAULT = frozenset({
    "file_viewer",
    "filesystem",
    "code_executor",
    "code_executor_v2",
    "transfer",
})


class MCPDevOnlyServerError(RuntimeError):
    """Raised at startup when a dev-only MCP server is enabled in production."""


def _normalize(name: str) -> str:
    return name.strip().lower().replace("-", "_")


def _is_dev_only(name: str, config: Mapping[str, Any]) -> bool:
    """Whether this server counts as development-only."""
    declared = config.get("dev_only")
    if isinstance(declared, bool):
        return declared  # Explicit setting always wins, in both directions.
    return _normalize(name) in DEV_ONLY_BY_DEFAULT


def find_enabled_dev_only_servers(servers_config: Mapping[str, Any]) -> List[str]:
    """Return the names of enabled servers that are development-only.

    Args:
        servers_config: Mapping of server name to its config block, as loaded
            from mcp.json.
    """
    found: List[str] = []
    for name, config in (servers_config or {}).items():
        if not isinstance(config, Mapping):
            continue
        if not config.get("enabled", True):
            continue
        if _is_dev_only(name, config):
            found.append(name)
    return sorted(found)


def enforce_dev_only_mcp_servers(
    servers_config: Mapping[str, Any],
    app_settings: Any,
) -> None:
    """Refuse to continue startup if a dev-only server is enabled in production.

    "Production" means ``ENVIRONMENT`` is production, or debug mode is off --
    either signal is enough, so a deployment that sets only one of them is
    still covered.

    Args:
        servers_config: Mapping of server name to config, from mcp.json.
        app_settings: Loaded AppSettings.

    Raises:
        MCPDevOnlyServerError: If any development-only server is enabled.
    """
    environment = str(getattr(app_settings, "environment", "") or "").lower()
    debug_mode = bool(getattr(app_settings, "debug_mode", False))
    is_production = environment == "production" or not debug_mode

    offenders = find_enabled_dev_only_servers(servers_config)
    if not offenders:
        return

    if not is_production:
        logger.warning(
            "Development-only MCP server(s) enabled: %s. These grant host "
            "filesystem or network access to any authenticated user and must "
            "not be enabled outside local development.",
            ", ".join(offenders),
        )
        return

    listed = ", ".join(offenders)
    logger.error(
        "SECURITY: development-only MCP server(s) enabled outside development: %s. "
        "These grant arbitrary host file access or outbound requests to every "
        "authenticated user. Refusing to start.",
        listed,
    )
    raise MCPDevOnlyServerError(
        f"Development-only MCP server(s) enabled in a non-development "
        f"environment: {listed}. Remove them from mcp.json, or set "
        f'"dev_only": false on a server you have reviewed and hardened. '
        f"See docs/admin/mcp-servers.md."
    )


def annotate_dev_only(servers_config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the config with an explicit ``dev_only`` on every server.

    Used by the admin UI and config API so an operator can see which servers
    the guard considers development-only without having to know the default
    list.
    """
    annotated = {}
    for name, config in (servers_config or {}).items():
        if isinstance(config, Mapping):
            entry = dict(config)
            entry["dev_only"] = _is_dev_only(name, config)
            annotated[name] = entry
        else:
            annotated[name] = config
    return annotated
