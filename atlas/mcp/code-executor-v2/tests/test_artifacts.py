"""Artifact diff + MIME packaging."""
import base64
import time
from pathlib import Path

from artifacts import diff_artifacts, mime_for, pick_primary, snapshot_mtimes


def test_mime_known_extensions():
    assert mime_for("a.png") == ("image/png", "image")
    assert mime_for("b.csv") == ("text/csv", "code")
    assert mime_for("c.unknown") == ("application/octet-stream", "auto")


def test_diff_returns_only_new_files(tmp_path: Path):
    (tmp_path / "old.txt").write_text("old")
    before = snapshot_mtimes(tmp_path)
    time.sleep(0.05)
    (tmp_path / "new.txt").write_text("new")
    arts = diff_artifacts(tmp_path, before=before, artifact_cap_bytes=1024)
    names = [a["name"] for a in arts]
    assert "new.txt" in names
    assert "old.txt" not in names


def test_diff_returns_modified_files(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("a")
    before = snapshot_mtimes(tmp_path)
    time.sleep(0.05)
    p.write_text("b")
    arts = diff_artifacts(tmp_path, before=before, artifact_cap_bytes=1024)
    assert any(a["name"] == "x.txt" for a in arts)


def test_oversize_artifact_referenced_not_inlined(tmp_path: Path):
    big = tmp_path / "huge.bin"
    big.write_bytes(b"\0" * 2048)
    arts = diff_artifacts(tmp_path, before={}, artifact_cap_bytes=1024)
    assert len(arts) == 1
    assert arts[0].get("oversize") is True
    assert "b64" not in arts[0]


def test_inline_artifact_decodes(tmp_path: Path):
    payload = b"hello world"
    (tmp_path / "x.txt").write_bytes(payload)
    arts = diff_artifacts(tmp_path, before={}, artifact_cap_bytes=1024)
    assert len(arts) == 1
    assert base64.b64decode(arts[0]["b64"]) == payload


def test_pick_primary_prefers_image(tmp_path: Path):
    arts = [
        {"name": "data.csv", "viewer": "code"},
        {"name": "plot.png", "viewer": "image"},
    ]
    assert pick_primary(arts) == "plot.png"


def test_pick_primary_falls_back(tmp_path: Path):
    arts = [{"name": "data.csv", "viewer": "code"}]
    assert pick_primary(arts) == "data.csv"


def test_pick_primary_empty():
    assert pick_primary([]) is None
