"""file_ops path-safety + cap tests."""
from pathlib import Path

import pytest

from file_ops import (
    WorkspaceError,
    delete_path,
    list_dir,
    read_file,
    workspace_bytes_used,
    write_file,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def test_write_then_read_text(workspace: Path):
    write_file(
        workspace, "hello.txt",
        content="hi", workspace_cap_bytes=10_000,
    )
    content, is_b64 = read_file(workspace, "hello.txt", max_bytes=100)
    assert is_b64 is False
    assert content == "hi"


def test_path_traversal_blocked(workspace: Path):
    with pytest.raises(WorkspaceError):
        write_file(
            workspace, "../escape.txt",
            content="x", workspace_cap_bytes=10_000,
        )


def test_workspace_cap_blocks_overflow(workspace: Path):
    big = "x" * 1024
    write_file(workspace, "a.txt", content=big, workspace_cap_bytes=2048)
    with pytest.raises(WorkspaceError):
        write_file(
            workspace, "b.txt",
            content=big * 4,
            workspace_cap_bytes=2048,
        )


def test_ls_returns_entries(workspace: Path):
    write_file(workspace, "a.txt", content="1", workspace_cap_bytes=1024)
    write_file(workspace, "b.bin", content_base64="AAA=", workspace_cap_bytes=1024)
    entries = list_dir(workspace)
    names = sorted(e["name"] for e in entries)
    assert names == ["a.txt", "b.bin"]


def test_delete_file(workspace: Path):
    write_file(workspace, "a.txt", content="1", workspace_cap_bytes=1024)
    delete_path(workspace, "a.txt")
    assert not (workspace / "a.txt").exists()


def test_delete_root_refused(workspace: Path):
    with pytest.raises(WorkspaceError):
        delete_path(workspace, "")


def test_max_bytes_enforced_on_read(workspace: Path):
    write_file(
        workspace, "big.txt",
        content="x" * 1000,
        workspace_cap_bytes=10_000,
    )
    with pytest.raises(WorkspaceError):
        read_file(workspace, "big.txt", max_bytes=10)


def test_workspace_bytes_used(workspace: Path):
    write_file(workspace, "a", content="abcde", workspace_cap_bytes=1024)
    assert workspace_bytes_used(workspace) == 5


def test_binary_returned_as_b64(workspace: Path):
    write_file(
        workspace, "x.bin",
        content_base64="qrvN3w==",  # 0xAA 0xBB 0xCD 0xDF
        workspace_cap_bytes=1024,
    )
    content, is_b64 = read_file(workspace, "x.bin", max_bytes=100, encoding=None)
    assert is_b64 is True
    assert content == "qrvN3w=="
