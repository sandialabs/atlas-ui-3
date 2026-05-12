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
async def test_bare_command_resolves_via_server_path(tmp_path, monkeypatch):
    """A bare command name should resolve against the server's PATH.

    The child's PATH is pinned to /usr/local/bin:/usr/bin:/bin, so if
    we did not pre-resolve the binary, anything installed under e.g.
    ~/.local/bin (or a venv) would fail with ENOENT even though it
    would have worked interactively on the server user's shell.
    """
    import os
    import stat

    # Put an executable named ``mycmd-xyz`` in a dir that will NOT be on
    # the child's pinned PATH, and expose that dir on the server PATH.
    custom_bin = tmp_path / "custom_bin"
    custom_bin.mkdir()
    script = custom_bin / "mycmd-xyz"
    script.write_text("#!/bin/sh\necho resolved-ok\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    monkeypatch.setenv("PATH", f"{custom_bin}:/usr/bin:/bin")

    manager = ProcessManager()
    managed = await manager.launch(command="mycmd-xyz")

    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert managed.status == ProcessStatus.EXITED, (
        f"expected EXITED, got {managed.status}; "
        f"command recorded as {managed.command}; "
        f"history: {[c.text for c in managed.history]}"
    )
    assert managed.exit_code == 0
    # The manager should have recorded the absolute path it resolved.
    assert os.path.isabs(managed.command)
    assert managed.command == str(script)
    stdout_lines = [c.text for c in managed.history if c.stream == "stdout"]
    assert "resolved-ok" in stdout_lines


@pytest.mark.asyncio
async def test_command_dir_is_added_to_child_path(tmp_path, monkeypatch):
    """The launched binary's own directory must be on the child PATH so
    that a shebang interpreter (``node``, ``python``, etc.) installed
    alongside it can be resolved by ``/usr/bin/env <interp>``.

    Without this, nvm/venv/uv-installed CLIs fail with a misleading
    exit 127 even though the binary itself was found.
    """
    import stat

    # Put two files in the same dir: a "binary" (a shell script that
    # tries to run our fake "interp"), and the "interp" itself.
    bin_dir = tmp_path / "tools_bin"
    bin_dir.mkdir()
    interp = bin_dir / "fake-interp"
    interp.write_text("#!/bin/sh\necho interp-found\n")
    interp.chmod(interp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    binary = bin_dir / "mytool"
    # Mimic an nvm-style shebang: /usr/bin/env <interp>. env will look
    # up `fake-interp` on the *child's* PATH, which by default is
    # pinned and would not include this dir.
    binary.write_text("#!/usr/bin/env fake-interp\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Server PATH does NOT include bin_dir; user passes the absolute
    # path so the parent finds it without shutil.which.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    manager = ProcessManager()
    managed = await manager.launch(command=str(binary))

    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert managed.status == ProcessStatus.EXITED, (
        f"expected EXITED, got {managed.status}; "
        f"history: {[(c.stream, c.text) for c in managed.history]}"
    )
    assert managed.exit_code == 0
    stdout = [c.text for c in managed.history if c.stream == "stdout"]
    assert "interp-found" in stdout


@pytest.mark.asyncio
async def test_missing_bare_command_raises_with_clear_message(monkeypatch):
    """When a bare command isn't on the server's PATH, the error should
    name the command and say where to look, not just echo ENOENT."""
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    manager = ProcessManager()
    with pytest.raises(FileNotFoundError) as excinfo:
        await manager.launch(command="definitely-not-a-real-binary-xyzzy")
    msg = str(excinfo.value)
    assert "definitely-not-a-real-binary-xyzzy" in msg
    assert "server PATH" in msg


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
async def test_sandbox_requires_cwd():
    manager = ProcessManager()
    with pytest.raises(ValueError):
        await manager.launch(
            command=sys.executable,
            args=["-c", "pass"],
            sandbox_mode="strict",
        )
    with pytest.raises(ValueError):
        await manager.launch(
            command=sys.executable,
            args=["-c", "pass"],
            sandbox_mode="workspace-write",
        )


@pytest.mark.asyncio
async def test_invalid_sandbox_mode_rejected():
    manager = ProcessManager()
    with pytest.raises(ValueError):
        await manager.launch(
            command=sys.executable,
            args=["-c", "pass"],
            sandbox_mode="bogus",
        )


@pytest.mark.asyncio
async def test_workspace_write_allows_reads_outside_cwd(tmp_path):
    """workspace-write mode lets the child read system files but not write outside cwd."""
    import os

    from atlas.modules.process_manager import landlock_is_supported

    if not landlock_is_supported():
        pytest.skip("Landlock not supported on this kernel")

    manager = ProcessManager()
    escape_path = "/tmp/atlas-workspace-write-escape-xyz.txt"
    if os.path.exists(escape_path):
        os.unlink(escape_path)

    managed = await manager.launch(
        command="bash",
        args=[
            "-c",
            # Reading /etc/hostname should succeed in workspace-write
            # mode (it would fail in strict mode outside /etc since /etc
            # is explicitly allowed — but pick a path where strict also
            # differs, like a file under $HOME).
            f"cat /etc/hostname > /dev/null && echo READ_OK && "
            f"(echo bad > {escape_path} 2>&1 || echo BLOCKED)",
        ],
        cwd=str(tmp_path),
        sandbox_mode="workspace-write",
    )
    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    stdout_texts = [c.text for c in managed.history if c.stream == "stdout"]
    assert "READ_OK" in stdout_texts
    assert "BLOCKED" in stdout_texts
    assert not os.path.exists(escape_path)


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
        sandbox_mode="strict",
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


@pytest.mark.asyncio
async def test_child_env_is_isolated(monkeypatch):
    """Child process must see a minimal env: allow-listed keys plus the
    pinned PATH, with no secret-shaped variables from the backend."""
    # Seed the parent env with values the backend might plausibly hold.
    secrets = {
        "AWS_ACCESS_KEY_ID": "AKIA_TEST",
        "AWS_SECRET_ACCESS_KEY": "wouldneverleak",
        "ANTHROPIC_API_KEY": "sk-ant-test",
        "OPENAI_API_KEY": "sk-openai-test",
        "ATLAS_DB_URL": "postgres://u:p@h/d",
        "GOOGLE_APPLICATION_CREDENTIALS": "/tmp/gcp.json",
        "PYTHONPATH": "/srv/atlas",
        "LD_PRELOAD": "/tmp/evil.so",
        "LD_LIBRARY_PATH": "/tmp",
        "VIRTUAL_ENV": "/srv/atlas/.venv",
        "SOME_API_TOKEN": "token-val",
        "MY_DB_PASSWORD": "pw-val",
    }
    for k, v in secrets.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HOME", "/home/testhome")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    manager = ProcessManager()
    managed = await manager.launch(
        command="/usr/bin/env",
    )
    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    assert managed.status == ProcessStatus.EXITED
    assert managed.exit_code == 0

    stdout = "\n".join(c.text for c in managed.history if c.stream == "stdout")
    env_keys = {line.split("=", 1)[0] for line in stdout.splitlines() if "=" in line}

    # PATH ends with the pinned defaults; the resolved command's
    # directory is prepended so shebang interpreters can be found.
    path_line = next(line for line in stdout.splitlines() if line.startswith("PATH="))
    assert path_line.endswith("/usr/local/bin:/usr/bin:/bin"), path_line
    assert "HOME" in env_keys
    assert "LANG" in env_keys
    for denied in secrets:
        assert denied not in env_keys, f"{denied} leaked to child"


@pytest.mark.asyncio
async def test_launch_strips_namespaces_when_host_unsupported(monkeypatch, caplog):
    """Caller passes namespaces=True but the host can't honour it.

    The frontend grays the toggle when /capabilities reports no
    namespace support, but a stale preset or a direct API call can
    still set the flag. The manager must strip it and proceed rather
    than fail with EPERM inside unshare(1). A warning is logged so the
    silent downgrade is auditable, and the recorded launch reflects
    the actual (non-isolated) execution.
    """
    from atlas.modules.process_manager import manager as pm_mod

    monkeypatch.setattr(
        pm_mod, "probe_isolation_capabilities", lambda: {"namespaces": False, "cgroups": False}
    )

    manager = ProcessManager()
    with caplog.at_level("WARNING", logger=pm_mod.__name__):
        managed = await manager.launch(
            command=sys.executable,
            args=["-c", "print('ok')"],
            namespaces=True,
            isolate_network=True,
        )
    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert managed.status == ProcessStatus.EXITED
    assert managed.exit_code == 0
    # The strip happened: the record must reflect no isolation so audit
    # / UI surfaces show the truth.
    assert managed.namespaces is False
    assert managed.isolate_network is False
    assert any(
        "stripping namespaces=true" in rec.getMessage() for rec in caplog.records
    ), "expected warning about stripped namespace flag"


@pytest.mark.asyncio
async def test_silence_watchdog_emits_system_hint_when_no_output():
    """A process that exists but stays silent should get a hint chunk.

    Without this, a wedged sandboxed tool (Landlock blocking its data
    dir, etc.) looks identical to a healthy start — both show a blank
    pane. The watchdog records a system chunk after a short threshold
    so the user has a starting point.

    Tests call ``_silence_watchdog`` directly with a tiny threshold so
    the behavior is exercised without depending on the production 5s
    timing (which would make the suite slow and flaky on busy CI).
    """
    manager = ProcessManager()
    managed = await manager.launch(
        command=sys.executable,
        args=["-c", "import time; time.sleep(2.0)"],
    )
    # Sanity: launch did NOT auto-record a system "No output" chunk
    # (the production 5s schedule hasn't fired yet).
    assert not any(
        c.stream == "system" and "No output" in c.text
        for c in managed.history
    )

    await manager._silence_watchdog(
        managed, threshold_seconds=0.2, kind="early"
    )
    assert any(
        c.stream == "system" and "No output" in c.text
        for c in managed.history
    ), "expected the early hint to be recorded"

    # Late kind emits a longer multi-line diagnostic.
    await manager._silence_watchdog(
        managed, threshold_seconds=0.2, kind="late"
    )
    late = [
        c for c in managed.history
        if c.stream == "system" and "Still no output" in c.text
    ]
    assert late, "expected the late diagnostic to be recorded"
    assert "Extra writable paths" in late[-1].text, (
        "late hint must mention extra writable paths so the user knows "
        "where to look"
    )


@pytest.mark.asyncio
async def test_silence_watchdog_silent_when_process_produces_output():
    """A chatty process must NOT get a silence-watchdog hint.

    Guards against the watchdog over-firing: any real stdout/stderr
    activity advances last_activity, and the watchdog must skip its
    hint in that case.
    """
    import time

    manager = ProcessManager()
    managed = await manager.launch(
        command=sys.executable,
        args=["-c", "import time; time.sleep(2.0)"],
    )
    # Simulate the child having produced real output. _record_chunk
    # flips has_real_output for any non-system stream, which is the
    # signal the watchdog reads.
    manager._record_chunk(managed, "stdout", "alive")
    assert managed.has_real_output is True

    await manager._silence_watchdog(
        managed, threshold_seconds=0.2, kind="early"
    )
    silence_chunks = [
        c for c in managed.history
        if c.stream == "system" and "No output" in c.text
    ]
    assert silence_chunks == [], (
        "watchdog must not emit when the process has produced output; "
        f"got {len(silence_chunks)} unexpected chunks"
    )


@pytest.mark.asyncio
async def test_silence_watchdog_skips_when_process_already_ended():
    """If the process exited before the threshold, no hint is recorded.

    Otherwise a fast-failing command (sandbox-setup error, missing
    binary) gets its own clear "Process ended" banner *plus* a
    spurious "No output yet" — confusing.
    """
    manager = ProcessManager()
    managed = await manager.launch(
        command=sys.executable,
        args=["-c", "pass"],  # exits ~immediately
    )
    # Wait for the process to finish naturally.
    for _ in range(60):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    assert managed.status != ProcessStatus.RUNNING

    await manager._silence_watchdog(
        managed, threshold_seconds=0.1, kind="early"
    )
    assert not any(
        c.stream == "system" and "No output" in c.text
        for c in managed.history
    ), "watchdog must skip when the process has already ended"


@pytest.mark.asyncio
async def test_extra_writable_paths_accepts_existing_file(tmp_path):
    """A single existing file in extra_writable_paths should be granted
    write access to that one file only — without granting write to its
    parent dir.

    Motivation: tools like Claude Code keep a state file at $HOME root
    (~/.claude.json). The old wrapper called makedirs() on every entry
    and silently dropped existing-file entries; this test pins the new
    file-aware behavior in place so it doesn't regress.
    """
    import os

    from atlas.modules.process_manager import landlock_is_supported

    if not landlock_is_supported():
        pytest.skip("Landlock not supported on this kernel")

    # State file outside cwd, plus a sibling we should NOT be able to
    # write (proves the rule is scoped to the file, not the parent).
    state_dir = tmp_path / "fakehome"
    state_dir.mkdir()
    state_file = state_dir / "state.json"
    state_file.write_text("{}")
    sibling = state_dir / "sibling.txt"
    sibling.write_text("untouched")

    workdir = tmp_path / "work"
    workdir.mkdir()

    manager = ProcessManager()
    managed = await manager.launch(
        command="bash",
        args=[
            "-c",
            # 1) Write to the granted file: should succeed (echo writes
            #    OK_FILE on success, BLOCKED_FILE on EACCES).
            # 2) Write to a sibling inside the same dir: must fail.
            f"(echo updated > {state_file} && echo OK_FILE) || echo BLOCKED_FILE; "
            f"(echo evil > {sibling} 2>&1 && echo OK_SIBLING) || echo BLOCKED_SIBLING",
        ],
        cwd=str(workdir),
        sandbox_mode="workspace-write",
        extra_writable_paths=[str(state_file)],
    )
    for _ in range(80):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)
    stdout = [c.text for c in managed.history if c.stream == "stdout"]
    assert "OK_FILE" in stdout, (
        f"file-scoped rule did not grant write; stdout={stdout}"
    )
    assert "BLOCKED_SIBLING" in stdout, (
        f"file-scoped rule leaked to parent dir; stdout={stdout}"
    )
    assert state_file.read_text().strip() == "updated"
    assert sibling.read_text() == "untouched"


@pytest.mark.asyncio
async def test_launch_skips_capability_probe_when_namespaces_false(monkeypatch):
    """Strip path only runs when the caller requested namespaces.

    Guards against the strip becoming over-eager: a launch with
    namespaces=False must not call probe_isolation_capabilities at
    all (it would be wasted work and could mask a regression where
    the gate is no longer scoped to namespaces=True).
    """
    from atlas.modules.process_manager import manager as pm_mod

    calls = {"n": 0}

    def _probe():
        calls["n"] += 1
        return {"namespaces": False, "cgroups": False}

    monkeypatch.setattr(pm_mod, "probe_isolation_capabilities", _probe)

    manager = ProcessManager()
    managed = await manager.launch(
        command=sys.executable,
        args=["-c", "print('ok')"],
        namespaces=False,
    )
    for _ in range(50):
        if managed.status != ProcessStatus.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert managed.status == ProcessStatus.EXITED
    assert calls["n"] == 0, "probe should not run when namespaces=False"
