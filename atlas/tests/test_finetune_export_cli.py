"""Tests for the fine-tune export CLI (issue #622)."""

import json
import tempfile
from pathlib import Path

import pytest

from atlas.application.chat.capture.capture_store import CaptureStore
from atlas.domain.capture.models import CapturedTurn, Label, Trajectory
from atlas.finetune_export_cli import main


def _seed(root: Path):
    store = CaptureStore(root, user_salt="cli")
    uh = store.user_hash("a@b.c")
    consent = {"user_hash": uh, "consent_version": 1, "system_flag_version": 1}
    # SFT-only turn
    store.append_turn(CapturedTurn(
        turn_id="t1", conversation_id="c", kind="turn", consent=consent, model="m",
        system_prompt="sys", messages_prefix=[{"role": "user", "content": "hi"}],
        available_tools=[{"name": "search", "schema": {}}],
        chosen=Trajectory(tool_calls=[{"name": "search", "arguments": {}}]),
        label=Label(),
    ))
    # DPO pair
    store.append_turn(CapturedTurn(
        turn_id="t2", conversation_id="c", kind="pair", consent=consent, model="m",
        system_prompt="sys", messages_prefix=[{"role": "user", "content": "hi"}],
        available_tools=[{"name": "search", "schema": {}}],
        chosen=Trajectory(tool_calls=[{"name": "search", "arguments": {}}]),
        rejected=Trajectory(tool_calls=[{"name": "fetch", "arguments": {}}]),
        label=Label(source="rollback", confidence=0.95),
    ))


@pytest.fixture
def seeded_dir():
    with tempfile.TemporaryDirectory() as tmp:
        _seed(Path(tmp))
        yield Path(tmp)


def _run(seeded_dir, fmt, capsys):
    rc = main(["--format", fmt, "--capture-dir", str(seeded_dir)])
    out = capsys.readouterr().out.strip().splitlines()
    return rc, [json.loads(line) for line in out if line]


def test_raw_emits_all(seeded_dir, capsys):
    rc, rows = _run(seeded_dir, "raw", capsys)
    assert rc == 0
    assert len(rows) == 2


def test_sft_emits_chosen_completion(seeded_dir, capsys):
    rc, rows = _run(seeded_dir, "sft", capsys)
    assert rc == 0
    assert len(rows) == 2  # both have a chosen side
    assert rows[0]["completion"]["tool_calls"][0]["name"] == "search"
    assert rows[0]["prompt"][0]["role"] == "system"


def test_dpo_only_emits_pairs(seeded_dir, capsys):
    rc, rows = _run(seeded_dir, "dpo", capsys)
    assert rc == 0
    assert len(rows) == 1  # only the record with a rejected side
    assert rows[0]["rejected"]["tool_calls"][0]["name"] == "fetch"
    assert rows[0]["chosen"]["tool_calls"][0]["name"] == "search"


def test_missing_dir_returns_error(capsys):
    rc = main(["--format", "raw", "--capture-dir", "/nonexistent/capture/dir"])
    assert rc == 1
