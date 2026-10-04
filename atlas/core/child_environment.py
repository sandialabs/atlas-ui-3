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


def _build_child_env(
    extra: Optional[Dict[str, str]] = None,
    *,
    extra_path_dirs: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Build an allowlisted environment, stripping secret-shaped extras.

    ``extra_path_dirs`` lets an absolute command find an adjacent shebang
    interpreter without inheriting the backend's full PATH. Callers accepting
    trusted operator-declared secrets must merge them *after* this baseline;
    user-controlled Agent Portal extras must retain the deny-list.
    """
    env: Dict[str, str] = {}
    for key in _ENV_ALLOW_EXACT:
        value = os.environ.get(key)
        if value is not None:
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
    if dropped:
        logger.info(
            "agent_portal env isolation dropped %d key(s): %s",
            len(dropped),
            sanitize_for_logging(",".join(sorted(dropped))),
        )
    return env
