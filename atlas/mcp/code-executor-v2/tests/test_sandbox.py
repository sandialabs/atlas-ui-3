"""End-to-end sandbox enforcement tests.

These tests actually execute the launcher under the kernel sandbox.
Skipped when the kernel does not support all required features so CI
on weak kernels still passes (the production server refuses to start
in that case anyway).
"""
from pathlib import Path

import pytest

from sandbox.kernel_probe import probe_kernel
from sandbox.launcher import SandboxLimits, run_sandboxed


KERNEL = probe_kernel()
SKIP_REASON = (
    f"kernel does not support full sandbox (landlock={KERNEL.landlock}, "
    f"userns/netns={KERNEL.user_and_net_namespace})"
)


pytestmark = pytest.mark.skipif(
    not KERNEL.all_supported,
    reason=SKIP_REASON,
)


def _limits() -> SandboxLimits:
    # Small wall clock so tests stay fast; mem big enough for python.
    return SandboxLimits(
        mem_mb=512, cpu_s=5, fsize_mb=8, nproc=32, wall_s=10,
    )


def test_sandbox_basic_python_works(tmp_path: Path):
    res = run_sandboxed(
        ["python", "-c", "print('hello')"],
        workdir=str(tmp_path),
        limits=_limits(),
    )
    assert res.returncode == 0
    assert "hello" in res.stdout


def test_network_is_blocked(tmp_path: Path):
    code = (
        "import socket, sys\n"
        "try:\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(2)\n"
        "    s.connect(('1.1.1.1', 80))\n"
        "    print('CONNECTED')\n"
        "    sys.exit(0)\n"
        "except Exception as e:\n"
        "    print(f'BLOCKED: {type(e).__name__}: {e}')\n"
        "    sys.exit(0)\n"
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=_limits(),
    )
    assert "CONNECTED" not in res.stdout
    assert "BLOCKED" in res.stdout


def test_dns_is_blocked(tmp_path: Path):
    code = (
        "import socket, sys\n"
        "try:\n"
        "    print('IP:', socket.gethostbyname('example.com'))\n"
        "except Exception as e:\n"
        "    print(f'BLOCKED: {type(e).__name__}')\n"
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=_limits(),
    )
    assert "IP:" not in res.stdout


def test_cannot_write_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "escape.txt"
    code = (
        f"open({str(outside)!r}, 'w').write('x')\n"
        "print('WROTE')\n"
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=_limits(),
    )
    # Either Landlock blocks it or the path is outside the visible mount;
    # either way the write must not succeed.
    assert "WROTE" not in res.stdout
    assert not outside.exists()


def test_can_write_inside_workspace(tmp_path: Path):
    code = "open('inside.txt', 'w').write('hi'); print('OK')"
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=_limits(),
    )
    assert res.returncode == 0
    assert (tmp_path / "inside.txt").read_text() == "hi"


def test_can_read_etc_passwd(tmp_path: Path):
    """System files are readable for normal program execution."""
    code = (
        "with open('/etc/passwd') as f:\n"
        "    data = f.read()\n"
        "print('len:', len(data))\n"
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=_limits(),
    )
    assert res.returncode == 0
    assert "len:" in res.stdout


def test_cannot_write_etc(tmp_path: Path):
    code = (
        "import sys\n"
        "try:\n"
        "    open('/etc/atlas-escape', 'w').write('x')\n"
        "    print('WROTE')\n"
        "except PermissionError as e:\n"
        "    print('BLOCKED')\n"
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=_limits(),
    )
    assert "WROTE" not in res.stdout


def test_wall_clock_timeout(tmp_path: Path):
    code = "import time; time.sleep(60); print('DONE')"
    limits = SandboxLimits(
        mem_mb=256, cpu_s=60, fsize_mb=8, nproc=8, wall_s=2,
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=limits,
    )
    assert res.timed_out
    assert "DONE" not in res.stdout


def test_memory_limit_enforced(tmp_path: Path):
    code = (
        "x = bytearray(2_000_000_000)\n"
        "print('ALLOC OK')\n"
    )
    limits = SandboxLimits(
        mem_mb=128, cpu_s=10, fsize_mb=8, nproc=8, wall_s=10,
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=limits,
    )
    assert "ALLOC OK" not in res.stdout


def test_fsize_limit_enforced(tmp_path: Path):
    code = (
        "with open('big.bin', 'wb') as f:\n"
        "    f.write(b'x' * (5 * 1024 * 1024))\n"
        "print('WROTE BIG')\n"
    )
    limits = SandboxLimits(
        mem_mb=256, cpu_s=10, fsize_mb=1, nproc=8, wall_s=10,
    )
    res = run_sandboxed(
        ["python", "-c", code], workdir=str(tmp_path), limits=limits,
    )
    assert "WROTE BIG" not in res.stdout
