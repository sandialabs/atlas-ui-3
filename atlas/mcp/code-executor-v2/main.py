#!/usr/bin/env python3
"""Code Executor v2 — stateful, sandboxed Python execution MCP (HTTP).

Refuses to start unless the kernel supports Landlock + unprivileged
user/network namespaces (override with ``CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX=1``
for local development only).

Tool surface:
    python(code, timeout?)
    upload_file(filename, file_data_base64?, file_url?)
    ls(path?)
    read_file(path, max_bytes?, encoding?)
    write_file(path, content?, content_base64?)
    delete_file(path)
    download_file(path)
    info()
    reset_session()
    git_clone(repo_url, pat?, ref?, subdir?)   # gated, only when enabled
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import os
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Dict, Optional
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from fastmcp import Context, FastMCP

# ---------------------------------------------------------------------------
# Module-local imports (self-contained: kept module-relative so the Docker
# image can be built from *this directory alone* without the rest of the
# Atlas monorepo).
# ---------------------------------------------------------------------------
from artifacts import diff_artifacts, mime_for, pick_primary, snapshot_mtimes
from config import load_config
from file_ops import (
    WorkspaceError,
    delete_path,
    list_dir,
    read_file as fs_read_file,
    workspace_bytes_used,
    write_file as fs_write_file,
)
from git_clone import run_git_clone
from sandbox.kernel_probe import probe_kernel
from sandbox.launcher import SandboxLimits, run_sandboxed
from session import SessionRegistry
from state_store import get_state_store


logger = logging.getLogger("code-executor-v2")
logging.basicConfig(
    level=os.environ.get("CODE_EXECUTOR_V2_LOG_LEVEL", "INFO"),
    format="%(asctime)s - code-executor-v2 - %(levelname)s - %(message)s",
)


CONFIG = load_config()
KERNEL = probe_kernel()


def _enforce_kernel_precondition() -> None:
    if KERNEL.all_supported:
        return
    msg = (
        "FATAL: Code Executor v2 requires kernel sandbox support.\n"
        f"  Landlock supported:        {KERNEL.landlock}\n"
        f"  User+net namespace usable: {KERNEL.user_and_net_namespace}\n"
        "Set CODE_EXECUTOR_V2_ALLOW_UNSAFE_NO_SANDBOX=1 to override (dev only)."
    )
    if CONFIG.allow_unsafe_no_sandbox:
        logger.warning(
            "%s — running unsandboxed because ALLOW_UNSAFE_NO_SANDBOX=1", msg
        )
        return
    sys.stderr.write(msg + "\n")
    sys.exit(1)


_enforce_kernel_precondition()


REGISTRY = SessionRegistry(
    workspaces_dir=Path(CONFIG.workspaces_dir),
    ttl_s=CONFIG.session_ttl_s,
    max_sessions=CONFIG.max_sessions,
    reaper_interval_s=CONFIG.reaper_interval_s,
)


@asynccontextmanager
async def _lifespan(server: "FastMCP") -> AsyncIterator[Dict[str, Any]]:
    """Start/stop the SessionRegistry on the server's serving event loop.

    Using FastMCP's lifespan hook (rather than running ``REGISTRY.start()``
    on a separate ``new_event_loop`` before ``mcp.run()``) guarantees the
    reaper task is created on the same loop that handles HTTP requests,
    so it actually executes.
    """
    await REGISTRY.start()
    try:
        yield {"registry": REGISTRY}
    finally:
        try:
            await REGISTRY.stop()
        except Exception as e:
            logger.warning("registry shutdown error: %s", e)


mcp = FastMCP(
    "Code Executor v2",
    session_state_store=get_state_store(),
    lifespan=_lifespan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _limits() -> SandboxLimits:
    return SandboxLimits(
        mem_mb=CONFIG.mem_mb,
        cpu_s=CONFIG.cpu_s,
        fsize_mb=CONFIG.fsize_mb,
        nproc=CONFIG.nproc,
        wall_s=CONFIG.wall_s,
    )


async def _session_for(ctx: Context):
    """Resolve the FastMCP session id for this call.

    The session id MUST come from the transport — that is what makes the
    workspace stable across tool calls within one conversation. We do
    *not* fall back to a per-call UUID, because that would silently
    produce one orphan workspace per request and defeat the stateful
    contract.
    """
    try:
        sid = ctx.session_id
    except RuntimeError as e:
        raise RuntimeError(
            "Code Executor v2 requires a per-conversation session id from "
            "the MCP transport (use the streamable-http transport)."
        ) from e
    if not sid:
        raise RuntimeError(
            "Empty MCP session id; refusing to synthesize a per-call id "
            "(would defeat stateful workspace contract)."
        )
    return await REGISTRY.get_or_create(sid)


def _envelope(
    *,
    results: Dict[str, Any],
    meta: Dict[str, Any],
    artifacts: Optional[list] = None,
    open_canvas: bool = False,
    primary_file: Optional[str] = None,
    viewer_hint: str = "auto",
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"results": results, "meta_data": meta}
    if artifacts:
        out["artifacts"] = artifacts
        out["display"] = {
            "open_canvas": open_canvas,
            "primary_file": primary_file or pick_primary(artifacts),
            "mode": "append",
            "viewer_hint": viewer_hint,
        }
    return out


def _truncate(s: str, max_chars: int = 4000) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"\n[truncated; {len(s)} chars total]"


_REMOTE_FETCH_MAX_BYTES = 64 * 1024 * 1024  # hard cap regardless of artifact_cap


def _backend_base_url() -> str:
    """Return the configured Atlas backend base URL (no trailing slash)."""
    return os.environ.get("CHATUI_BACKEND_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def _resolve_upload_url(url: str) -> str:
    """Validate ``url`` for ``upload_file`` and return the absolute URL.

    Rules:
      * Only ``http(s)`` is accepted; ``file://``, ``ftp://``, ``data://``,
        etc. are rejected outright.
      * Backend-relative paths (``/api/files/...``) resolve against
        ``CHATUI_BACKEND_BASE_URL``.
      * The destination host must be either the configured backend or an
        explicit allow-list (``CODE_EXECUTOR_V2_UPLOAD_ALLOWED_HOSTS``).
      * The destination must not resolve to a loopback / private / link-
        local / multicast / reserved address (defeats SSRF to internal
        services), unless the resolved host *is* the configured backend
        host (which is allowed to be a private/internal address).
    """
    if not url:
        raise ValueError("file_url is empty")
    if url.startswith("/"):
        url = _backend_base_url() + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"unsupported url scheme {parsed.scheme!r}; only http(s) allowed"
        )
    host = parsed.hostname
    if not host:
        raise ValueError("file_url has no host")

    backend_host = urlparse(_backend_base_url()).hostname
    extra_hosts = {
        h.strip().lower()
        for h in os.environ.get(
            "CODE_EXECUTOR_V2_UPLOAD_ALLOWED_HOSTS", ""
        ).split(",")
        if h.strip()
    }
    allowed_hosts = {h for h in (backend_host,) if h} | extra_hosts
    if host.lower() not in allowed_hosts:
        raise ValueError(
            f"host {host!r} not in upload allow-list "
            f"(set CODE_EXECUTOR_V2_UPLOAD_ALLOWED_HOSTS to extend)"
        )

    # SSRF defense: every IP the host resolves to must either match the
    # backend's own host or be globally routable. The backend-host itself
    # is allowed to be private/loopback (that's the whole point), but no
    # other allowed entry may point at internal infrastructure.
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ValueError(f"failed to resolve {host!r}: {e}") from e
    is_backend_host = (host.lower() == (backend_host or "").lower())
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        unsafe = (
            ip.is_loopback or ip.is_private or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        )
        if unsafe and not is_backend_host:
            raise ValueError(
                f"host {host!r} resolves to non-public address {addr}; "
                "refusing to fetch (SSRF guard)"
            )
    return url


def _load_remote_bytes(url: str) -> bytes:
    """Fetch a URL into bytes for ``upload_file``.

    Hard-restricted: see ``_resolve_upload_url`` for scheme + host policy.
    The response is read with a byte cap so a malicious or runaway server
    cannot exhaust pod memory.
    """
    safe_url = _resolve_upload_url(url)
    req = Request(safe_url)
    with urlopen(req, timeout=20) as resp:  # noqa: S310 — guarded above
        data = resp.read(_REMOTE_FETCH_MAX_BYTES + 1)
    if len(data) > _REMOTE_FETCH_MAX_BYTES:
        raise ValueError(
            f"remote file exceeds {_REMOTE_FETCH_MAX_BYTES} bytes; refusing"
        )
    return data


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
@mcp.tool
async def python(
    code: Annotated[str, "Python code to execute"],
    ctx: Context,
    timeout: Annotated[
        int, "Wall-clock timeout in seconds (capped by server config)"
    ] = 0,
) -> Dict[str, Any]:
    """Execute Python in the session's sandboxed workspace.

    Each call is a fresh subprocess under Landlock + network namespace +
    rlimits; **state survives across calls only via files in the
    workspace**, not Python globals. Returns stdout/stderr/returncode
    plus any files newly created or modified during this call as v2
    artifacts.

    Constraints:
        * No network access.
        * Filesystem writes are restricted to the session workspace.
        * Memory / CPU / file-size capped by server config.
        * Each call gets a fresh interpreter.
        * Use ``upload_file`` to bring data in, ``download_file`` to take
          something out.
    """
    record = await _session_for(ctx)
    record.last_seen_mtimes = snapshot_mtimes(record.workspace)
    limits = _limits()
    if timeout > 0:
        limits = SandboxLimits(
            mem_mb=limits.mem_mb,
            cpu_s=min(limits.cpu_s, timeout),
            fsize_mb=limits.fsize_mb,
            nproc=limits.nproc,
            wall_s=min(limits.wall_s, timeout),
        )

    # Wrap user code with auto-savefig of any open matplotlib figures
    wrapped = (
        "import sys, traceback\n"
        "_user_src = " + repr(code) + "\n"
        "_g = {'__name__': '__main__'}\n"
        "try:\n"
        "    exec(compile(_user_src, '<user>', 'exec'), _g)\n"
        "except SystemExit:\n"
        "    raise\n"
        "except BaseException:\n"
        "    traceback.print_exc()\n"
        "    sys.exit(1)\n"
        "try:\n"
        "    import matplotlib.pyplot as _plt\n"
        "    for _n in _plt.get_fignums():\n"
        "        try:\n"
        "            _plt.figure(_n).savefig(f'plot_{_n}.png')\n"
        "        except Exception:\n"
        "            pass\n"
        "    _plt.close('all')\n"
        "except Exception:\n"
        "    pass\n"
    )

    start = time.monotonic()
    result = run_sandboxed(
        ["python", "-c", wrapped],
        workdir=str(record.workspace),
        limits=limits,
    )
    elapsed = round(time.monotonic() - start, 4)

    # Enforce the workspace cap *after* the run. RLIMIT_FSIZE caps each
    # individual file written by the child, but a long-running script can
    # write many files; without this check, user code can fill the pod's
    # disk regardless of CODE_EXECUTOR_V2_WS_CAP_MB.
    ws_used = workspace_bytes_used(record.workspace)
    workspace_cap_exceeded = ws_used > CONFIG.workspace_cap_bytes
    if workspace_cap_exceeded:
        logger.warning(
            "session %s exceeded workspace cap (%d > %d) — wiping workspace",
            record.session_id, ws_used, CONFIG.workspace_cap_bytes,
        )
        try:
            await REGISTRY.reset(record.session_id)
        except Exception as e:
            logger.error("failed to reset over-cap workspace: %s", e)
        artifacts: list = []
        ws_used = workspace_bytes_used(record.workspace)
    else:
        artifacts = diff_artifacts(
            record.workspace,
            before=record.last_seen_mtimes,
            artifact_cap_bytes=CONFIG.artifact_cap_bytes,
        )

    is_error = (
        result.returncode != 0 or result.timed_out or workspace_cap_exceeded
    )
    results = {
        "stdout": _truncate(result.stdout),
        "stderr": _truncate(result.stderr),
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "summary": (
            "Execution timed out"
            if result.timed_out
            else "Workspace cap exceeded; workspace was reset"
            if workspace_cap_exceeded
            else (
                "Execution completed successfully"
                if not is_error else "Execution failed"
            )
        ),
    }
    meta = {
        "is_error": is_error,
        "execution_time_sec": elapsed,
        "wall_seconds": result.wall_seconds,
        "session_id": record.session_id,
        "sandbox": {
            "fs": "landlock",
            "net": "namespace-blackhole",
            "mem_mb": limits.mem_mb,
            "cpu_s": limits.cpu_s,
            "fsize_mb": limits.fsize_mb,
        },
        "workspace_bytes_used": ws_used,
        "workspace_cap_exceeded": workspace_cap_exceeded,
        "artifact_count": len(artifacts),
    }
    return _envelope(
        results=results,
        meta=meta,
        artifacts=artifacts,
        open_canvas=bool(artifacts) and not is_error,
        viewer_hint="image" if any(
            a.get("viewer") == "image" for a in artifacts
        ) else "auto",
    )


@mcp.tool
async def upload_file(
    filename: Annotated[str, "Destination filename inside the workspace"],
    ctx: Context,
    file_data_base64: Annotated[str, "Base64-encoded file content"] = "",
    file_url: Annotated[
        str, "Optional URL to fetch (backend-relative paths supported)"
    ] = "",
) -> Dict[str, Any]:
    """Upload a file into the session workspace.

    Provide exactly one of ``file_data_base64`` or ``file_url``.
    """
    record = await _session_for(ctx)
    if bool(file_data_base64) == bool(file_url):
        return _envelope(
            results={"error": "supply exactly one of file_data_base64 or file_url"},
            meta={"is_error": True},
        )
    try:
        if file_data_base64:
            data = base64.b64decode(file_data_base64, validate=True)
        else:
            data = _load_remote_bytes(file_url)
    except Exception as e:
        return _envelope(
            results={"error": f"failed to load file: {e}"},
            meta={"is_error": True},
        )
    safe_name = os.path.basename(filename)
    if not safe_name:
        return _envelope(
            results={"error": "invalid filename"}, meta={"is_error": True}
        )
    try:
        written = fs_write_file(
            record.workspace,
            safe_name,
            content_base64=base64.b64encode(data).decode("ascii"),
            workspace_cap_bytes=CONFIG.workspace_cap_bytes,
        )
    except WorkspaceError as e:
        return _envelope(results={"error": str(e)}, meta={"is_error": True})
    return _envelope(
        results={"uploaded": safe_name, "bytes": written},
        meta={
            "is_error": False,
            "session_id": record.session_id,
            "workspace_bytes_used": workspace_bytes_used(record.workspace),
        },
    )


@mcp.tool
async def ls(
    ctx: Context,
    path: Annotated[str, "Sub-path inside workspace (default: root)"] = "",
) -> Dict[str, Any]:
    """List files in the session workspace (or a sub-directory)."""
    record = await _session_for(ctx)
    try:
        entries = list_dir(record.workspace, path)
    except WorkspaceError as e:
        return _envelope(results={"error": str(e)}, meta={"is_error": True})
    return _envelope(
        results={"path": path or "/", "entries": entries},
        meta={
            "is_error": False,
            "session_id": record.session_id,
            "workspace_bytes_used": workspace_bytes_used(record.workspace),
        },
    )


@mcp.tool
async def read_file(
    path: Annotated[str, "File path within workspace"],
    ctx: Context,
    max_bytes: Annotated[int, "Max bytes to read"] = 1_000_000,
    encoding: Annotated[
        str, "Text encoding (empty -> binary returned as base64)"
    ] = "utf-8",
) -> Dict[str, Any]:
    """Read a file from the workspace."""
    record = await _session_for(ctx)
    try:
        content, is_b64 = fs_read_file(
            record.workspace, path,
            max_bytes=max_bytes,
            encoding=encoding or None,
        )
    except WorkspaceError as e:
        return _envelope(results={"error": str(e)}, meta={"is_error": True})
    return _envelope(
        results={
            "path": path,
            "encoding": "base64" if is_b64 else (encoding or "utf-8"),
            "content": content,
        },
        meta={"is_error": False, "session_id": record.session_id},
    )


@mcp.tool
async def write_file(
    path: Annotated[str, "Destination path within workspace"],
    ctx: Context,
    content: Annotated[str, "Text content (utf-8)"] = "",
    content_base64: Annotated[str, "Base64-encoded binary content"] = "",
) -> Dict[str, Any]:
    """Write a file into the workspace.

    Provide exactly one of ``content`` (text) or ``content_base64`` (binary).
    """
    record = await _session_for(ctx)
    if bool(content) == bool(content_base64):
        return _envelope(
            results={"error": "supply exactly one of content or content_base64"},
            meta={"is_error": True},
        )
    try:
        bytes_written = fs_write_file(
            record.workspace,
            path,
            content=content or None,
            content_base64=content_base64 or None,
            workspace_cap_bytes=CONFIG.workspace_cap_bytes,
        )
    except WorkspaceError as e:
        return _envelope(results={"error": str(e)}, meta={"is_error": True})
    return _envelope(
        results={"path": path, "bytes": bytes_written},
        meta={
            "is_error": False,
            "session_id": record.session_id,
            "workspace_bytes_used": workspace_bytes_used(record.workspace),
        },
    )


@mcp.tool
async def delete_file(
    path: Annotated[str, "Path within workspace to delete"],
    ctx: Context,
) -> Dict[str, Any]:
    """Delete a file or directory inside the workspace."""
    record = await _session_for(ctx)
    try:
        info = delete_path(record.workspace, path)
    except WorkspaceError as e:
        return _envelope(results={"error": str(e)}, meta={"is_error": True})
    return _envelope(
        results=info,
        meta={
            "is_error": False,
            "session_id": record.session_id,
            "workspace_bytes_used": workspace_bytes_used(record.workspace),
        },
    )


@mcp.tool
async def download_file(
    path: Annotated[str, "File within workspace to return as artifact"],
    ctx: Context,
) -> Dict[str, Any]:
    """Return a workspace file as a v2 artifact for the user to download."""
    record = await _session_for(ctx)
    try:
        content, is_b64 = fs_read_file(
            record.workspace, path,
            max_bytes=CONFIG.artifact_cap_bytes,
            encoding=None,
        )
    except WorkspaceError as e:
        return _envelope(results={"error": str(e)}, meta={"is_error": True})
    name = os.path.basename(path)
    mime, viewer = mime_for(name)
    artifact = {
        "name": name,
        "b64": content if is_b64 else base64.b64encode(content.encode()).decode("ascii"),
        "mime": mime,
        "viewer": viewer,
        "size": len(base64.b64decode(content)) if is_b64 else len(content),
        "description": f"Downloaded from workspace: {path}",
    }
    return _envelope(
        results={"path": path, "delivered_as_artifact": True},
        meta={"is_error": False, "session_id": record.session_id},
        artifacts=[artifact],
        open_canvas=True,
        viewer_hint=viewer,
    )


@mcp.tool
async def info(ctx: Context) -> Dict[str, Any]:
    """Report installed packages, sandbox status, limits, and session info."""
    record = await _session_for(ctx)
    packages = _list_installed_packages()
    return _envelope(
        results={
            "session_id": record.session_id,
            "workspace": str(record.workspace),
            "workspace_bytes_used": workspace_bytes_used(record.workspace),
            "kernel": {
                "landlock": KERNEL.landlock,
                "user_and_net_namespace": KERNEL.user_and_net_namespace,
                "unsafe_override": CONFIG.allow_unsafe_no_sandbox,
            },
            "limits": {
                "mem_mb": CONFIG.mem_mb,
                "cpu_s": CONFIG.cpu_s,
                "fsize_mb": CONFIG.fsize_mb,
                "nproc": CONFIG.nproc,
                "wall_s": CONFIG.wall_s,
                "workspace_cap_mb": CONFIG.workspace_cap_mb,
                "artifact_cap_mb": CONFIG.artifact_cap_mb,
                "session_ttl_s": CONFIG.session_ttl_s,
            },
            "git_clone_enabled": CONFIG.enable_git_clone,
            "installed_packages": packages,
            "registry": REGISTRY.stats(),
        },
        meta={"is_error": False},
    )


@mcp.tool
async def reset_session(ctx: Context) -> Dict[str, Any]:
    """Wipe the session workspace and clear state."""
    record = await _session_for(ctx)
    await REGISTRY.reset(record.session_id)
    return _envelope(
        results={"reset": True, "session_id": record.session_id},
        meta={"is_error": False},
    )


if CONFIG.enable_git_clone:
    @mcp.tool
    async def git_clone(
        repo_url: Annotated[str, "Repository URL (https or ssh)"],
        ctx: Context,
        pat: Annotated[
            str, "Personal access token (injected via env, not logged)"
        ] = "",
        ref: Annotated[str, "Branch or tag to clone (default HEAD)"] = "HEAD",
        subdir: Annotated[
            str, "Workspace subdirectory (default: repo basename)"
        ] = "",
    ) -> Dict[str, Any]:
        """Shallow-clone a git repository into the workspace.

        This is the only tool that runs with network access enabled; all
        other sandbox layers (Landlock, rlimits, workspace-only writes,
        NO_NEW_PRIVS) still apply. The PAT is passed via env var, never
        argv.
        """
        record = await _session_for(ctx)
        out = run_git_clone(
            workspace=record.workspace,
            repo_url=repo_url,
            pat=pat or None,
            ref=ref,
            subdir=subdir or None,
        )
        return _envelope(
            results=out,
            meta={
                "is_error": not out.get("ok", False),
                "session_id": record.session_id,
                "workspace_bytes_used": workspace_bytes_used(record.workspace),
            },
        )


def _list_installed_packages() -> Dict[str, str]:
    """Best-effort listing of importable third-party packages."""
    packages: Dict[str, str] = {}
    candidates = [
        "numpy", "pandas", "polars", "pyarrow", "duckdb", "openpyxl",
        "scipy", "statsmodels", "sklearn", "sympy", "matplotlib",
        "seaborn", "plotly", "PIL", "cv2", "networkx", "shapely",
        "bs4", "lxml", "jinja2", "joblib", "tqdm",
    ]
    for name in candidates:
        try:
            mod = __import__(name)
            packages[name] = getattr(mod, "__version__", "?")
        except Exception:
            continue
    return packages


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    # Registry start/stop is wired via the FastMCP lifespan above so the
    # reaper task lives on the same loop that serves HTTP requests.
    mcp.run(
        transport="streamable-http",
        host=CONFIG.host,
        port=CONFIG.port,
        show_banner=False,
    )


if __name__ == "__main__":
    main()
