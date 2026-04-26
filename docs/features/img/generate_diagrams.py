"""Generate PNG diagrams documenting the Agent Portal substrate.

Runs offline (no network, no browser). Produced assets are checked in
alongside `docs/features/agent-portal.md` so the review surface is
self-contained and reproducible.

Usage:
    python docs/features/img/generate_diagrams.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT_DIR = Path(__file__).parent


def _box(ax, x, y, w, h, text, *, color="#f2f4f8", edge="#4f5d75", fontsize=10):
    box = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.2,
        facecolor=color,
        edgecolor=edge,
    )
    ax.add_patch(box)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontsize=fontsize,
        color="#1c1f23",
        family="DejaVu Sans",
    )


def _arrow(ax, x1, y1, x2, y2, *, label=None, color="#3c4148"):
    arrow = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle="-|>",
        mutation_scale=14,
        linewidth=1.1,
        color=color,
    )
    ax.add_patch(arrow)
    if label:
        ax.text(
            (x1 + x2) / 2,
            (y1 + y2) / 2 + 0.08,
            label,
            ha="center",
            va="bottom",
            fontsize=8,
            color=color,
            family="DejaVu Sans",
        )


def architecture() -> None:
    fig, ax = plt.subplots(figsize=(10, 5.4), dpi=160)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.4)
    ax.set_axis_off()

    ax.text(
        5, 5.05,
        "Agent Portal — v0 control plane",
        ha="center", fontsize=13, weight="bold", color="#1c1f23",
    )
    ax.text(
        5, 4.75,
        "Mounted only when FEATURE_AGENT_PORTAL_ENABLED=true",
        ha="center", fontsize=9, color="#4f5d75", style="italic",
    )

    _box(ax, 0.3, 3.4, 2.2, 0.9, "HTTP client\n(/api/agent-portal/*)", color="#e8f0fe")
    _box(ax, 3.0, 3.4, 2.3, 0.9, "agent_portal_routes\n(FastAPI)", color="#e8f0fe")
    _box(ax, 5.8, 3.4, 2.3, 0.9, "AgentPortalService\n(policy gate)", color="#fff4e5")
    _box(ax, 8.6, 3.4, 1.2, 0.9, "feature_flag\ncheck", color="#fff4e5", fontsize=9)

    _arrow(ax, 2.5, 3.85, 3.0, 3.85)
    _arrow(ax, 5.3, 3.85, 5.8, 3.85)
    _arrow(ax, 8.1, 3.85, 8.6, 3.85)

    _box(ax, 0.3, 1.9, 2.2, 0.9, "SessionManager\n(state machine)", color="#ecf7e8")
    _box(ax, 3.0, 1.9, 2.3, 0.9, "SandboxProfile\n(Landlock + net + seccomp)", color="#ecf7e8")
    _box(ax, 5.8, 1.9, 2.3, 0.9, "bubblewrap\nargv builder (pure fn)", color="#ecf7e8")
    _box(ax, 8.6, 1.9, 1.2, 0.9, "RuntimeAdapter\nprotocol", color="#ecf7e8", fontsize=9)

    _arrow(ax, 6.9, 3.4, 1.4, 2.8)
    _arrow(ax, 6.9, 3.4, 4.1, 2.8)
    _arrow(ax, 6.9, 3.4, 6.9, 2.8)
    _arrow(ax, 6.9, 3.4, 9.2, 2.8)

    _box(ax, 0.3, 0.4, 2.2, 0.9, "local_process\nadapter (v0)", color="#f3e8f7")
    _box(ax, 3.0, 0.4, 2.3, 0.9, "ssh+tmux adapter\n(deferred)", color="#efefef")
    _box(ax, 5.8, 0.4, 2.3, 0.9, "kubernetes adapter\n(deferred)", color="#efefef")
    _box(ax, 8.6, 0.4, 1.2, 0.9, "slurm\n(deferred)", color="#efefef", fontsize=9)

    _box(ax, 0.3, 0.4 - 0.02, 9.5, 0.02, "", color="#4f5d75", edge="#4f5d75")

    _box(ax, 1.1, 0.4 - 0.85, 7.8, 0.6, "AuditStream — one SHA-256 chained JSONL per session", color="#fde7e9", fontsize=10)

    fig.tight_layout()
    fig.savefig(OUT_DIR / "architecture.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def state_machine() -> None:
    fig, ax = plt.subplots(figsize=(12, 5.4), dpi=160)
    ax.set_xlim(0, 12)
    ax.set_ylim(0, 5.4)
    ax.set_axis_off()

    ax.text(
        6, 5.1,
        "Session state machine (forward-only, terminal fan-out)",
        ha="center", fontsize=12, weight="bold", color="#1c1f23",
    )

    # Main happy-path row (y=3.2) with explicit x positions and good spacing.
    row_y = 3.2
    nodes = [
        ("pending",        0.9,  row_y, "#e8f0fe"),
        ("authenticating", 2.7,  row_y, "#e8f0fe"),
        ("launching",      4.6,  row_y, "#fff4e5"),
        ("running",        6.5,  row_y, "#ecf7e8"),
        ("ending",         8.4,  row_y, "#fff4e5"),
        ("ended",         10.6,  row_y, "#dbeedb"),
        # Terminal fan-out below.
        ("failed",         7.5,  1.3,   "#fde7e9"),
        ("reaped",        10.0,  1.3,   "#f3e8f7"),
    ]
    for name, cx, cy, color in nodes:
        _box(ax, cx - 0.75, cy - 0.3, 1.5, 0.6, name, color=color, fontsize=9)

    node_map = {n[0]: (n[1], n[2]) for n in nodes}
    edges_forward = [
        ("pending", "authenticating"),
        ("authenticating", "launching"),
        ("launching", "running"),
        ("running", "ending"),
        ("ending", "ended"),
    ]
    for a, b in edges_forward:
        x1, y1 = node_map[a]
        x2, y2 = node_map[b]
        _arrow(ax, x1 + 0.75, y1, x2 - 0.75, y2)

    # failed fan-out
    for a in ("launching", "running", "ending"):
        x1, y1 = node_map[a]
        fx, fy = node_map["failed"]
        _arrow(ax, x1, y1 - 0.3, fx - 0.1, fy + 0.3, color="#b0443a")

    # reaped fan-out (only from running; watchdog)
    rx, ry = node_map["running"]
    tx, ty = node_map["reaped"]
    _arrow(ax, rx + 0.2, ry - 0.3, tx - 0.4, ty + 0.3, color="#6a4598")

    ax.text(
        6, 0.4,
        "red arrows: failed (from any non-terminal state).   "
        "purple arrow: reaped (watchdog on budget overrun).",
        ha="center", fontsize=9, color="#4f5d75", style="italic",
    )

    fig.tight_layout()
    fig.savefig(OUT_DIR / "state_machine.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def sandbox_tiers() -> None:
    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=160)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.8)
    ax.set_axis_off()

    ax.text(
        5, 3.55,
        "Sandbox tier matrix (defaults shipped)",
        ha="center", fontsize=12, weight="bold", color="#1c1f23",
    )

    headers = ["tier", "filesystem", "network", "clear_env", "seccomp"]
    rows = [
        ["restrictive", "read-only binds + ephemeral RW scratch",
         "denied (unshare-net)", "true", "strict"],
        ["standard", "project bind + RW scratch",
         "allowlist_proxy", "true", "default"],
        ["permissive", "host FS (developer opt-in)",
         "unrestricted", "false", "off"],
    ]
    row_colors = ["#ecf7e8", "#fff4e5", "#fde7e9"]

    col_x = [0.2, 1.6, 4.3, 6.4, 7.5, 8.7]
    col_w = [col_x[i + 1] - col_x[i] - 0.1 for i in range(5)]

    y = 2.8
    for i, header in enumerate(headers):
        _box(ax, col_x[i], y, col_w[i], 0.55, header, color="#e2e6ef", fontsize=10)

    for r, row in enumerate(rows):
        y = 2.15 - r * 0.7
        for i, cell in enumerate(row):
            _box(ax, col_x[i], y, col_w[i], 0.6, cell, color=row_colors[r], fontsize=9)

    fig.tight_layout()
    fig.savefig(OUT_DIR / "sandbox_tiers.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def audit_frame_anatomy() -> None:
    fig, ax = plt.subplots(figsize=(10, 3.6), dpi=160)
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 3.6)
    ax.set_axis_off()

    ax.text(
        5, 3.4,
        "Audit frame anatomy (one JSONL line per event)",
        ha="center", fontsize=12, weight="bold", color="#1c1f23",
    )

    frame_text = (
        '{"ts":"2026-04-21T17:02:14.081Z",\n'
        ' "session":"5a9e1f…",\n'
        ' "seq":12,\n'
        ' "prev":"b3c1…e47d",   <-- SHA-256 of previous canonical frame\n'
        ' "stream":"tool",\n'
        ' "tool":"mcp.fs.read", "args_sha":"e8…", "result_size":184}\n'
    )

    ax.text(
        0.3, 2.5, frame_text,
        fontsize=10, family="monospace", color="#1c1f23",
        bbox=dict(boxstyle="round,pad=0.6", facecolor="#f7fafc", edgecolor="#4f5d75"),
        va="center",
    )

    bullets = [
        "stream ∈ {stdin, stdout, stderr, tool, lifecycle, policy}",
        "prev chains every frame; tamper with any one → verify_chain() raises",
        "opaque bytes go through data_b64; structured fields stay top-level",
        "writer is thread-safe (per-process Lock); fsync + chmod best-effort",
    ]
    for i, b in enumerate(bullets):
        ax.text(0.3, 0.95 - i * 0.22, "• " + b, fontsize=9.5, color="#4f5d75")

    fig.tight_layout()
    fig.savefig(OUT_DIR / "audit_frame.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    architecture()
    state_machine()
    sandbox_tiers()
    audit_frame_anatomy()
    print(f"Wrote diagrams into {OUT_DIR}")
