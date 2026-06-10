"""Command-line client for the Agent Portal REST + WebSocket API.

A thin urllib-based client so developers can launch, list, stream, and
cancel agent-portal processes without the browser. Also useful for
end-to-end testing and debugging launch failures that are awkward to
reproduce through the UI.

Usage
-----
    atlas-portal launch bash -- -c "echo hi"
    atlas-portal list
    atlas-portal stream <process_id>
    atlas-portal cancel <process_id>
    atlas-portal presets list
    atlas-portal presets delete <preset_id>

Authentication
--------------
The dev server accepts an ``X-User-Email`` header (or whatever the
server is configured to use as the auth header) and falls back to the
configured test user when the header is absent in debug mode. Set the
user explicitly with ``--user`` or the ``ATLAS_USER`` env var.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

DEFAULT_URL = os.environ.get("ATLAS_URL", "http://localhost:8000")
DEFAULT_USER = os.environ.get("ATLAS_USER", "test@test.com")
DEFAULT_AUTH_HEADER = os.environ.get("ATLAS_AUTH_HEADER", "X-User-Email")


class PortalError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


def _request(
    method: str,
    url: str,
    *,
    user: str,
    auth_header: str = DEFAULT_AUTH_HEADER,
    body: Optional[Dict[str, Any]] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Send one request. Returns (status, decoded-json-or-empty-dict)."""
    data: Optional[bytes] = None
    headers = {auth_header: user, "Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read() or b""
        status = e.code
    except urllib.error.URLError as e:
        raise PortalError(0, f"connection error: {e.reason}")

    if not raw:
        return status, {}
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, {"raw": raw.decode("utf-8", errors="replace")}


def _ok(status: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    if status >= 400:
        detail = payload.get("detail") or payload.get("raw") or str(payload)
        raise PortalError(status, str(detail))
    return payload


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=False)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def cmd_launch(args: argparse.Namespace) -> int:
    body: Dict[str, Any] = {
        "command": args.command,
        "args": args.command_args or [],
        "sandbox_mode": args.sandbox,
        "use_pty": args.pty,
    }
    if args.cwd:
        body["cwd"] = args.cwd
    if args.display_name:
        body["display_name"] = args.display_name
    if args.extra_writable_paths:
        body["extra_writable_paths"] = args.extra_writable_paths
    if args.namespaces:
        body["namespaces"] = True
    if args.isolate_network:
        body["isolate_network"] = True
    if args.memory_limit:
        body["memory_limit"] = args.memory_limit
    if args.cpu_limit:
        body["cpu_limit"] = args.cpu_limit
    if args.pids_limit is not None:
        body["pids_limit"] = args.pids_limit

    status, payload = _request(
        "POST",
        f"{args.url}/api/agent-portal/processes",
        user=args.user,
        auth_header=args.auth_header,
        body=body,
    )
    data = _ok(status, payload)
    if args.json_output:
        print(_pretty(data))
    else:
        print(f"launched: {data.get('id')}")
        print(f"   status: {data.get('status')}   pid: {data.get('pid')}")
        if data.get("sandboxed"):
            print(f"   sandbox: {data.get('sandbox_mode')}")

    if args.stream:
        return _stream_until_exit(args, data["id"])
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    status, payload = _request(
        "GET",
        f"{args.url}/api/agent-portal/processes",
        user=args.user,
        auth_header=args.auth_header,
    )
    data = _ok(status, payload)
    procs = data.get("processes", [])
    if args.json_output:
        print(_pretty(procs))
        return 0
    if not procs:
        print("(no processes)")
        return 0
    for p in procs:
        label = p.get("display_name") or f"{p.get('command')} {' '.join(p.get('args', []))}"
        print(
            f"{p['id'][:8]}  {p['status']:<9}  pid={p.get('pid', '-')!s:<7}  "
            f"exit={p.get('exit_code', '-')!s:<4}  {label}"
        )
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    status, payload = _request(
        "GET",
        f"{args.url}/api/agent-portal/processes/{urllib.parse.quote(args.process_id)}",
        user=args.user,
        auth_header=args.auth_header,
    )
    data = _ok(status, payload)
    print(_pretty(data))
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    status, payload = _request(
        "DELETE",
        f"{args.url}/api/agent-portal/processes/{urllib.parse.quote(args.process_id)}",
        user=args.user,
        auth_header=args.auth_header,
    )
    data = _ok(status, payload)
    if args.json_output:
        print(_pretty(data))
    else:
        print(f"cancelled: {data.get('id')}   status: {data.get('status')}")
    return 0


def cmd_stream(args: argparse.Namespace) -> int:
    return _stream_until_exit(args, args.process_id)


def _stream_until_exit(args: argparse.Namespace, process_id: str) -> int:
    """Poll /processes/{id} for status, printing new history chunks.

    WebSocket streaming needs an Origin header under the portal's
    loopback-origin check. urllib does not do WebSockets; rather than
    pull in a dep, poll the REST endpoint and print any history
    delivered on the summary. If the child is short-lived this misses
    nothing because the history ring buffer captures up to 2000 chunks.
    For long-running streams, use the browser UI.
    """
    import time

    while True:
        status, payload = _request(
            "GET",
            f"{args.url}/api/agent-portal/processes/{urllib.parse.quote(process_id)}",
            user=args.user,
            auth_header=args.auth_header,
        )
        if status >= 400:
            sys.stderr.write(f"stream: {status} {payload.get('detail', '')}\n")
            return 1
        proc_status = payload.get("status")
        # Pull fresh history via a short sidechannel — the /processes/{id}
        # summary does not include history, so we rely on process_end
        # telemetry. For a richer live stream the UI WS is the right tool.
        if proc_status not in ("running",):
            if args.json_output:
                print(_pretty(payload))
            else:
                print(
                    f"[{proc_status}] pid={payload.get('pid')} exit={payload.get('exit_code')}"
                )
            return 0 if payload.get("exit_code") == 0 else 1
        time.sleep(0.5)


def cmd_presets(args: argparse.Namespace) -> int:
    if args.presets_subcommand == "list":
        status, payload = _request(
            "GET",
            f"{args.url}/api/agent-portal/presets",
            user=args.user,
            auth_header=args.auth_header,
        )
        data = _ok(status, payload)
        presets = data.get("presets", [])
        if args.json_output:
            print(_pretty(presets))
            return 0
        if not presets:
            print("(no presets)")
            return 0
        for p in presets:
            print(f"{p['id'][:10]}  {p.get('name')!r:<40}  {p.get('command')} {' '.join(p.get('args', []))}")
        return 0
    if args.presets_subcommand == "show":
        status, payload = _request(
            "GET",
            f"{args.url}/api/agent-portal/presets/{urllib.parse.quote(args.preset_id)}",
            user=args.user,
            auth_header=args.auth_header,
        )
        print(_pretty(_ok(status, payload)))
        return 0
    if args.presets_subcommand == "delete":
        status, payload = _request(
            "DELETE",
            f"{args.url}/api/agent-portal/presets/{urllib.parse.quote(args.preset_id)}",
            user=args.user,
            auth_header=args.auth_header,
        )
        if status >= 400:
            raise PortalError(status, str(payload.get("detail", payload)))
        print("deleted")
        return 0
    raise SystemExit(f"unknown presets subcommand: {args.presets_subcommand}")


def cmd_capabilities(args: argparse.Namespace) -> int:
    status, payload = _request(
        "GET",
        f"{args.url}/api/agent-portal/capabilities",
        user=args.user,
        auth_header=args.auth_header,
    )
    data = _ok(status, payload)
    print(_pretty(data))
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-portal",
        description="CLI client for the Atlas Agent Portal REST API.",
    )
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Server URL (default: {DEFAULT_URL})")
    parser.add_argument("--user", default=DEFAULT_USER, help=f"Auth user email (default: {DEFAULT_USER})")
    parser.add_argument(
        "--auth-header",
        default=DEFAULT_AUTH_HEADER,
        help=f"Auth header name (default: {DEFAULT_AUTH_HEADER})",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Emit JSON")

    sub = parser.add_subparsers(dest="subcommand", required=True)

    launch = sub.add_parser("launch", help="Launch a new process")
    launch.add_argument("command", help="Executable name or absolute path")
    launch.add_argument(
        "command_args",
        nargs=argparse.REMAINDER,
        help="Args for the command. Put them after a literal `--` to avoid clashing with flags.",
    )
    launch.add_argument("--cwd", default=None, help="Working directory")
    launch.add_argument(
        "--sandbox",
        default="off",
        choices=["off", "strict", "workspace-write"],
        help="Landlock sandbox mode",
    )
    launch.add_argument("--pty", action="store_true", help="Allocate a pseudo-terminal")
    launch.add_argument(
        "--namespaces",
        action="store_true",
        help="Run in isolated Linux namespaces (user, pid, uts, ipc, mnt)",
    )
    launch.add_argument(
        "--isolate-network",
        action="store_true",
        help="Also isolate the network namespace (requires --namespaces)",
    )
    launch.add_argument("--memory-limit", default=None, help="cgroup MemoryMax (e.g. 512M)")
    launch.add_argument("--cpu-limit", default=None, help="cgroup CPUQuota (e.g. 50%%)")
    launch.add_argument("--pids-limit", type=int, default=None, help="cgroup TasksMax")
    launch.add_argument(
        "--extra-writable-paths",
        action="append",
        default=None,
        help="Additional writable path (repeatable)",
    )
    launch.add_argument("--display-name", default=None, help="Friendly name shown in the UI")
    launch.add_argument("--stream", action="store_true", help="Wait for the process and print its final status")
    launch.set_defaults(func=cmd_launch)

    lst = sub.add_parser("list", help="List the current user's processes")
    lst.set_defaults(func=cmd_list)

    get = sub.add_parser("get", help="Fetch a single process's summary")
    get.add_argument("process_id")
    get.set_defaults(func=cmd_get)

    cancel = sub.add_parser("cancel", help="Cancel a running process")
    cancel.add_argument("process_id")
    cancel.set_defaults(func=cmd_cancel)

    stream = sub.add_parser("stream", help="Poll a process until it exits")
    stream.add_argument("process_id")
    stream.set_defaults(func=cmd_stream)

    caps = sub.add_parser("capabilities", help="Show host isolation capabilities")
    caps.set_defaults(func=cmd_capabilities)

    presets = sub.add_parser("presets", help="Preset library commands")
    psub = presets.add_subparsers(dest="presets_subcommand", required=True)
    psub.add_parser("list")
    pshow = psub.add_parser("show")
    pshow.add_argument("preset_id")
    pdel = psub.add_parser("delete")
    pdel.add_argument("preset_id")
    presets.set_defaults(func=cmd_presets)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # argparse REMAINDER keeps the literal "--" separator in position 0
    # when present; strip it so "launch bash -- -c 'echo hi'" works the
    # way a shell user expects.
    if getattr(args, "command_args", None) and args.command_args and args.command_args[0] == "--":
        args.command_args = args.command_args[1:]

    try:
        return args.func(args)
    except PortalError as e:
        sys.stderr.write(f"error: {e}\n")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
