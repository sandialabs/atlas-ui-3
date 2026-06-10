"""Session registry + reaper behavior."""
import asyncio
from pathlib import Path

import pytest

from session import SessionRegistry


@pytest.mark.asyncio
async def test_create_returns_workspace(tmp_path: Path):
    reg = SessionRegistry(
        tmp_path, ttl_s=60, max_sessions=10, reaper_interval_s=10,
    )
    rec = await reg.get_or_create("s1")
    assert rec.workspace.exists()
    assert rec.workspace.name == "s1"


@pytest.mark.asyncio
async def test_get_or_create_idempotent(tmp_path: Path):
    reg = SessionRegistry(
        tmp_path, ttl_s=60, max_sessions=10, reaper_interval_s=10,
    )
    a = await reg.get_or_create("s1")
    b = await reg.get_or_create("s1")
    assert a.workspace == b.workspace


@pytest.mark.asyncio
async def test_max_sessions_enforced(tmp_path: Path):
    reg = SessionRegistry(
        tmp_path, ttl_s=60, max_sessions=2, reaper_interval_s=10,
    )
    await reg.get_or_create("a")
    await reg.get_or_create("b")
    with pytest.raises(RuntimeError):
        await reg.get_or_create("c")


@pytest.mark.asyncio
async def test_reset_wipes_workspace(tmp_path: Path):
    reg = SessionRegistry(
        tmp_path, ttl_s=60, max_sessions=10, reaper_interval_s=10,
    )
    rec = await reg.get_or_create("s1")
    (rec.workspace / "x.txt").write_text("hello")
    await reg.reset("s1")
    assert not (rec.workspace / "x.txt").exists()


@pytest.mark.asyncio
async def test_destroy_removes_record_and_dir(tmp_path: Path):
    reg = SessionRegistry(
        tmp_path, ttl_s=60, max_sessions=10, reaper_interval_s=10,
    )
    rec = await reg.get_or_create("s1")
    await reg.destroy("s1")
    assert not rec.workspace.exists()


@pytest.mark.asyncio
async def test_reaper_evicts_idle_sessions(tmp_path: Path):
    reg = SessionRegistry(
        tmp_path, ttl_s=0, max_sessions=10, reaper_interval_s=1,
    )
    await reg.start()
    try:
        rec = await reg.get_or_create("s1")
        # ttl=0 means anything older than now is expired; wait for reaper
        await asyncio.sleep(1.5)
        assert not rec.workspace.exists()
    finally:
        await reg.stop()


@pytest.mark.asyncio
async def test_start_wipes_pre_existing(tmp_path: Path):
    leftover = tmp_path / "leftover-session"
    leftover.mkdir()
    (leftover / "garbage.txt").write_text("x")
    reg = SessionRegistry(
        tmp_path, ttl_s=60, max_sessions=10, reaper_interval_s=10,
    )
    await reg.start()
    try:
        assert not leftover.exists()
    finally:
        await reg.stop()
