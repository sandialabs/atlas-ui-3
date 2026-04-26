"""Unit tests for the SHA-256-chained audit writer."""

import json

import pytest

from atlas.modules.agent_portal.audit import (
    AuditChainError,
    AuditStream,
    read_frames,
    verify_chain,
)


def test_chain_verifies_end_to_end(tmp_path):
    stream = AuditStream(path=tmp_path / "s.jsonl", session_id="abc")
    stream.append("lifecycle", payload={"event": "created"})
    stream.append("stdout", data=b"hello\n")
    stream.append("tool", payload={"tool": "read_file", "path": "/workspace/x"})
    result = verify_chain(stream.path)
    assert result["ok"] is True
    assert result["frames"] == 3


def test_sequence_numbers_are_monotonic(tmp_path):
    stream = AuditStream(path=tmp_path / "s.jsonl", session_id="abc")
    for i in range(5):
        stream.append("stdout", data=f"line {i}\n".encode())
    frames = list(read_frames(stream.path))
    assert [f["seq"] for f in frames] == [1, 2, 3, 4, 5]


def test_tampering_with_payload_breaks_chain(tmp_path):
    stream = AuditStream(path=tmp_path / "s.jsonl", session_id="abc")
    stream.append("lifecycle", payload={"event": "created"})
    stream.append("stdout", data=b"secret\n")
    stream.append("tool", payload={"tool": "read_file"})

    # Rewrite the middle frame's data while keeping its `prev` intact.
    lines = stream.path.read_bytes().splitlines()
    frame = json.loads(lines[1])
    frame["data_b64"] = "YXR0YWNrZXI="  # base64 of "attacker"
    lines[1] = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode()
    stream.path.write_bytes(b"\n".join(lines) + b"\n")

    with pytest.raises(AuditChainError):
        verify_chain(stream.path)


def test_first_frame_prev_is_genesis(tmp_path):
    stream = AuditStream(path=tmp_path / "s.jsonl", session_id="abc")
    stream.append("lifecycle", payload={"event": "created"})
    frames = list(read_frames(stream.path))
    assert frames[0]["prev"] == "0" * 64


def test_bytes_are_base64_encoded(tmp_path):
    stream = AuditStream(path=tmp_path / "s.jsonl", session_id="abc")
    stream.append("stdout", data=b"\xff\x00\x7f")
    frames = list(read_frames(stream.path))
    assert "data_b64" in frames[0]
    # decoding round-trips
    import base64
    assert base64.b64decode(frames[0]["data_b64"]) == b"\xff\x00\x7f"
