#!/usr/bin/env python3
"""PermissionRequest example: auto-approve read-only tools, escalate the rest.

Event:    PermissionRequest (execute_single_tool, at the approval gate --
          before PreToolUse runs).
Matcher:  the tool name.
Payload:  {"tool_name": str, "tool_args": {...}, "tool_call_id": str,
           "needs_approval": bool, "allow_edit": bool, "admin_required": bool}
Can do:   modify with {"needs_approval": false} (auto-approve) | modify
          tool_args | require_approval (force the gate) | deny | continue.
on_error: deny (per-event default).

This is the only event that may *lower* the gate, and it lowers it completely:
``needs_approval: false`` skips the approval request outright, admin-required
tools included. So an auto-approval must check ``admin_required`` itself, as
below -- the runtime will not do it for you. (Escalation is the asymmetric
case: ``require_approval`` never raises a request to admin-only, because a
non-admin user could not then satisfy it.)

A second scope rule: approve the *arguments* you inspected, not the tool name
alone. Reading any path is not the same decision as reading a project file, so
the read-only branch below still checks where the read points. And if a later
PreToolUse hook rewrites the arguments, Atlas revokes this auto-approval,
because it covered arguments that will not be the ones that run.
"""
import json
import os
import sys

READ_ONLY_TOOLS = ("filesystem__read_file", "filesystem__list_directory", "rag__search")
AUTO_APPROVED_ROOT = "/workspace"

env = json.load(sys.stdin)
payload = env.get("payload") or {}
tool = payload.get("tool_name") or ""
args = payload.get("tool_args") if isinstance(payload.get("tool_args"), dict) else {}


def _within_auto_approved_root(path: object) -> bool:
    """A missing path means the tool takes none (e.g. rag__search): allowed."""
    if path is None:
        return True
    normalized = os.path.normpath(str(path))
    return normalized == AUTO_APPROVED_ROOT or normalized.startswith(AUTO_APPROVED_ROOT + os.sep)


if tool.startswith("email__") or tool.startswith("http_"):
    print(json.dumps({
        "decision": "require_approval",
        "reason": "Outbound tool: a human must confirm this call",
    }))
elif (
    tool in READ_ONLY_TOOLS
    and not payload.get("admin_required")
    and _within_auto_approved_root(args.get("path"))
):
    print(json.dumps({
        "decision": "modify",
        "payload": {"needs_approval": False},
        "reason": f"Read-only tool under {AUTO_APPROVED_ROOT} auto-approved by policy",
    }))

sys.exit(0)
