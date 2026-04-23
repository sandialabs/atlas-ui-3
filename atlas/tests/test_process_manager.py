"""Tests for the Agent Portal process manager."""

import asyncio
import sys

import pytest

from atlas.modules.process_manager import (
    ProcessManager,
    ProcessNotFoundError,
    ProcessStatus,
)


@pytest.mark.asyncio
async def test_launch_and_collect_stdout():
    manager = ProcessManager()
    managed = await manager.launch(
        command=sys.executable,
        args=["-c", "print('hello'); print('world')"],
    )
    # Give the process a moment to finish
    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert managed.status == ProcessStatus.EXITED
    assert managed.exit_code == 0
    stdout_lines = [c.text for c in managed.history if c.stream == "stdout"]
    assert "hello" in stdout_lines
    assert "world" in stdout_lines


@pytest.mark.asyncio
async def test_failed_command_reports_error():
    manager = ProcessManager()
    with pytest.raises(FileNotFoundError):
        await manager.launch(command="/nonexistent/binary-xyz")


@pytest.mark.asyncio
async def test_list_filters_by_user():
    manager = ProcessManager()
    await manager.launch(
        command=sys.executable, args=["-c", "pass"], user_email="alice@x.com"
    )
    await manager.launch(
        command=sys.executable, args=["-c", "pass"], user_email="bob@x.com"
    )
    alice = manager.list_processes(user_email="alice@x.com")
    assert len(alice) == 1
    assert alice[0]["user_email"] == "alice@x.com"
    all_procs = manager.list_processes()
    assert len(all_procs) == 2


@pytest.mark.asyncio
async def test_subscribe_streams_live_output():
    manager = ProcessManager()
    # Long enough that we can subscribe before it finishes
    script = (
        "import sys,time\n"
        "for i in range(3):\n"
        "    print(f'line{i}', flush=True)\n"
        "    time.sleep(0.1)\n"
    )
    managed = await manager.launch(command=sys.executable, args=["-c", script])

    collected = []

    async def consume():
        async for chunk in manager.subscribe(managed.id):
            collected.append(chunk)

    await asyncio.wait_for(consume(), timeout=5.0)
    stdout_texts = [c.text for c in collected if c.stream == "stdout"]
    assert "line0" in stdout_texts
    assert "line2" in stdout_texts


@pytest.mark.asyncio
async def test_cancel_terminates_running_process():
    manager = ProcessManager()
    managed = await manager.launch(
        command=sys.executable,
        args=["-c", "import time; time.sleep(30)"],
    )
    assert managed.status == ProcessStatus.RUNNING

    await manager.cancel(managed.id)

    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    assert managed.status == ProcessStatus.CANCELLED


@pytest.mark.asyncio
async def test_get_missing_raises():
    manager = ProcessManager()
    with pytest.raises(ProcessNotFoundError):
        manager.get("no-such-id")


@pytest.mark.asyncio
async def test_invalid_cwd_rejected():
    manager = ProcessManager()
    with pytest.raises(ValueError):
        await manager.launch(command=sys.executable, cwd="/definitely/does/not/exist/xyz")


@pytest.mark.asyncio
async def test_empty_command_rejected():
    manager = ProcessManager()
    with pytest.raises(ValueError):
        await manager.launch(command="   ")


@pytest.mark.asyncio
async def test_restrict_to_cwd_requires_cwd():
    manager = ProcessManager()
    with pytest.raises(ValueError):
        await manager.launch(
            command=sys.executable,
            args=["-c", "pass"],
            restrict_to_cwd=True,
        )


@pytest.mark.asyncio
async def test_landlock_confines_writes_to_cwd(tmp_path):
    """Writes inside cwd succeed; writes outside it fail with EACCES."""
    import os
    from atlas.modules.process_manager import landlock_is_supported

    if not landlock_is_supported():
        pytest.skip("Landlock not supported on this kernel")

    manager = ProcessManager()
    escape_path = "/tmp/atlas-landlock-test-escape-xyz.txt"
    if os.path.exists(escape_path):
        os.unlink(escape_path)

    managed = await manager.launch(
        command="bash",
        args=[
            "-c",
            f"echo inside > inside.txt && (echo outside > {escape_path} 2>&1 || echo BLOCKED)",
        ],
        cwd=str(tmp_path),
        restrict_to_cwd=True,
    )
    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert managed.sandboxed is True
    assert (tmp_path / "inside.txt").exists()
    assert not os.path.exists(escape_path)
    stdout_texts = [c.text for c in managed.history if c.stream == "stdout"]
    assert "BLOCKED" in stdout_texts
