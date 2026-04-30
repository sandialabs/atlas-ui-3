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

import asyncio
import base64
import logging
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any, Dict, Optional
from urllib.request import Request, urlopen

from fastmcp import Context, FastMCP

# ---------------------------------------------------------------------------
# Module-local imports (self-contained: keep relative)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from atlas.mcp.common.state import get_state_store  # noqa: E402

from artifacts import diff_artifacts, mime_for, pick_primary, snapshot_mtimes  # noqa: E402
from config import load_config  # noqa: E402
from file_ops import (  # noqa: E402
    WorkspaceError,
    delete_path,
    list_dir,
    read_file as fs_read_file,
    workspace_bytes_used,
    write_file as fs_write_file,
)
from git_clone import run_git_clone  # noqa: E402
from sandbox.kernel_probe import probe_kernel  # noqa: E402
from sandbox.launcher import SandboxLimits, run_sandboxed  # noqa: E402
from session import SessionRegistry  # noqa: E402


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


mcp = FastMCP("Code Executor v2", session_state_store=get_state_store())


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
    try:
        sid = ctx.session_id
    except Exception:
        sid = None
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


def _load_remote_bytes(url: str) -> bytes:
    """Fetch a URL into bytes. Used by ``upload_file`` for backend-relative paths."""
    if url.startswith("/"):
        base = os.environ.get("CHATUI_BACKEND_BASE_URL", "http://127.0.0.1:8000")
        url = base.rstrip("/") + url
    req = Request(url)
    with urlopen(req, timeout=20) as resp:  # noqa: S310 — backend-controlled URL
        return resp.read()


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

    artifacts = diff_artifacts(
        record.workspace,
        before=record.last_seen_mtimes,
        artifact_cap_bytes=CONFIG.artifact_cap_bytes,
    )

    is_error = result.returncode != 0 or result.timed_out
    results = {
        "stdout": _truncate(result.stdout),
        "stderr": _truncate(result.stderr),
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "summary": (
            "Execution timed out"
            if result.timed_out
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
        "workspace_bytes_used": workspace_bytes_used(record.workspace),
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
async def _bootstrap() -> None:
    await REGISTRY.start()


async def _shutdown() -> None:
    await REGISTRY.stop()


def main() -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_bootstrap())
    except Exception:
        loop.close()
        raise
    try:
        mcp.run(
            transport="streamable-http",
            host=CONFIG.host,
            port=CONFIG.port,
            show_banner=False,
        )
    finally:
        try:
            loop.run_until_complete(_shutdown())
        except Exception as e:
            logger.warning("shutdown error: %s", e)
        loop.close()


if __name__ == "__main__":
    main()
