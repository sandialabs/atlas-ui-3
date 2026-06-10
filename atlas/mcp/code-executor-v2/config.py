"""Environment-driven configuration for code-executor-v2.

All knobs are read at import time. Tests can monkey-patch the module
constants directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ExecutorConfig:
    host: str
    port: int
    workspaces_dir: str
    workspace_cap_mb: int
    artifact_cap_mb: int
    session_ttl_s: int
    max_sessions: int
    mem_mb: int
    cpu_s: int
    fsize_mb: int
    nproc: int
    wall_s: int
    enable_git_clone: bool
    allow_unsafe_no_sandbox: bool
    reaper_interval_s: int

    @property
    def workspace_cap_bytes(self) -> int:
        return self.workspace_cap_mb * 1024 * 1024

    @property
    def artifact_cap_bytes(self) -> int:
        return self.artifact_cap_mb * 1024 * 1024


def load_config() -> ExecutorConfig:
    return ExecutorConfig(
        host=os.environ.get("MCP_CODE_EXECUTOR_V2_HOST", "0.0.0.0"),
        port=_env_int("MCP_CODE_EXECUTOR_V2_PORT", 8011),
        workspaces_dir=os.environ.get(
            "CODE_EXECUTOR_V2_WORKSPACES_DIR", "/workspaces"
        ),
        workspace_cap_mb=_env_int("CODE_EXECUTOR_V2_WS_CAP_MB", 256),
        artifact_cap_mb=_env_int("CODE_EXECUTOR_V2_ARTIFACT_CAP_MB", 10),
        session_ttl_s=_env_int("CODE_EXECUTOR_V2_SESSION_TTL_S", 3600),
        max_sessions=_env_int("CODE_EXECUTOR_V2_MAX_SESSIONS", 100),
        mem_mb=_env_int("CODE_EXECUTOR_V2_MEM_MB", 2048),
        cpu_s=_env_int("CODE_EXECUTOR_V2_CPU_S", 30),
        fsize_mb=_env_int("CODE_EXECUTOR_V2_FSIZE_MB", 256),
        nproc=_env_int("CODE_EXECUTOR_V2_NPROC", 64),
        wall_s=_env_int("CODE_EXECUTOR_V2_WALL_S", 60),
        enable_git_clone=_env_bool("CODE_EXECUTOR_V2_ENABLE_GIT_CLONE", False),
        allow_unsafe_no_sandbox=_env_bool(
            "CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX", False
        ),
        reaper_interval_s=_env_int("CODE_EXECUTOR_V2_REAPER_INTERVAL_S", 300),
    )
