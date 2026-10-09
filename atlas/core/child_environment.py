"""Portable environment isolation shared by subprocess launchers."""

import logging
import os
from typing import Dict, List, Optional

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

_ENV_ALLOW_EXACT = (
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "TERM",
    "TZ",
    "TMPDIR",
)

# Non-secret operational configuration that bundled MCP children need to reach
# the backend, share session state, and honor the host's proxy/CA settings.
# Opt in with ``forward_mcp_config=True``: some proxy and Redis URLs embed
# credentials, so forwarding them must be a deliberate choice.
_ENV_ALLOW_CONFIG_EXACT = (
    "CHATUI_BACKEND_BASE_URL",
    "BACKEND_URL",
    "MCP_STATE_BACKEND",
    "MCP_REDIS_URL",
    "MCP_SESSION_STATE_PORT",
    "MCP_CODE_EXECUTOR_V2_HOST",
    "PPTX_TEMPLATE_PATH",
    "CHATUI_RUNTIME_UPLOADS",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)
_ENV_ALLOW_CONFIG_PREFIXES = ("CODE_EXECUTOR_V2_", "MCP_TRANSFER_")

# Do not inherit the backend's virtualenv or arbitrary tool directories.
_ENV_FIXED_PATH = "/usr/local/bin:/usr/bin:/bin"
_ENV_DENY_SUFFIXES = ("_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_PASSWD")
_ENV_DENY_PREFIXES = (
    "AWS_",
    "GCP_",
    "ATLAS_",
    "ANTHROPIC_",
    "OPENAI_",
    "CONDA_",
)
_ENV_DENY_EXACT = frozenset(
    {
        "GOOGLE_APPLICATION_CREDENTIALS",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "PYTHONPATH",
        "VIRTUAL_ENV",
        "NODE_PATH",
    }
)


def _is_denied_env_key(key: str) -> bool:
    k = key.upper()
    if k in _ENV_DENY_EXACT:
        return True
    if any(k.startswith(p) for p in _ENV_DENY_PREFIXES):
        return True
    if any(k.endswith(s) for s in _ENV_DENY_SUFFIXES):
        return True
    return False


def _is_mcp_config_env_key(key: str) -> bool:
    """Bundled MCP servers read these non-secret keys from their environment."""
    return key in _ENV_ALLOW_CONFIG_EXACT or key.startswith(_ENV_ALLOW_CONFIG_PREFIXES)


def _build_child_env(
    extra: Optional[Dict[str, str]] = None,
    *,
    extra_path_dirs: Optional[List[str]] = None,
    forward_mcp_config: bool = False,
) -> Dict[str, str]:
    """Build an allowlisted environment, stripping secret-shaped extras.

    ``extra_path_dirs`` lets an absolute command find an adjacent shebang
    interpreter without inheriting the backend's full PATH. ``forward_mcp_config``
    is opt-in because the bundled MCP servers need backend URLs, shared-state
    settings, and proxy/CA variables whose values can embed credentials.
    Callers accepting trusted operator-declared secrets must merge them *after*
    this baseline; caller-supplied extras must retain the deny-list.
    """
    env: Dict[str, str] = {}
    for key in _ENV_ALLOW_EXACT:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if forward_mcp_config:
        for key, value in os.environ.items():
            if _is_mcp_config_env_key(key):
                env[key] = value
    for key, value in os.environ.items():
        if key.startswith("LC_"):
            env[key] = value
    path_parts: List[str] = []
    if extra_path_dirs:
        for d in extra_path_dirs:
            if d and d not in path_parts:
                path_parts.append(d)
    path_parts.extend(_ENV_FIXED_PATH.split(":"))
    env["PATH"] = ":".join(path_parts)
    if extra:
        env.update(extra)

    dropped: List[str] = []
    for key in list(env.keys()):
        if _is_denied_env_key(key):
            dropped.append(key)
            env.pop(key, None)
    # Name the backend keys the allowlist did not forward so a child failing to
    # reach a resource can be diagnosed without guessing which variable is
    # missing. Values are never logged; sanitize_for_logging hardens the names.
    withheld = sorted(k for k in os.environ if k not in env)
    if dropped:
        logger.info(
            "subprocess env isolation dropped %d key(s): %s",
            len(dropped),
            sanitize_for_logging(",".join(sorted(dropped))),
        )
    if withheld:
        logger.info(
            "subprocess env isolation withheld %d backend key(s): %s",
            len(withheld),
            sanitize_for_logging(",".join(withheld)),
        )
    return env
