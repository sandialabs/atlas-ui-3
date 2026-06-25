"""Tests for the fine-tune capture storage layer (issue #622).

Covers consent round-tripping, salted user hashing, JSONL append/read,
aggregate stats, self-delete, and filename safety against path traversal.
"""

import tempfile
from pathlib import Path

import pytest

from atlas.application.chat.capture.capture_store import CaptureStore
from atlas.domain.capture.models import CapturedTurn, Label, Trajectory


@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmp:
        yield CaptureStore(Path(tmp), user_salt="unit-test-salt")


def _turn(user_hash: str, kind: str = "turn", source: str = "implicit", rejected=None):
    return CapturedTurn(
        turn_id="t-" + user_hash,
        conversation_id="conv",
        kind=kind,
        consent={"user_hash": user_hash, "consent_version": 1, "system_flag_version": 1},
        model="m",
        chosen=Trajectory(tool_calls=[{"name": "search", "arguments": {}}]),
        rejected=rejected,
        label=Label(source=source, confidence=0.9 if source != "implicit" else 0.0),
    )


class TestConsent:
    def test_default_consent_is_opted_out(self, store):
        record = store.get_consent("nobody@example.com")
        assert record.enabled is False

    def test_set_and_get_consent_roundtrip(self, store):
        store.set_consent("alice@example.com", True, consent_version=3)
        record = store.get_consent("alice@example.com")
        assert record.enabled is True
        assert record.consent_version == 3
        assert record.consented_at is not None

    def test_revoke_records_revoked_at(self, store):
        store.set_consent("alice@example.com", True)
        store.set_consent("alice@example.com", False)
        record = store.get_consent("alice@example.com")
        assert record.enabled is False
        assert record.revoked_at is not None

    def test_email_is_normalized_for_hashing(self, store):
        assert store.user_hash("Alice@Example.com ") == store.user_hash("alice@example.com")


class TestUserHash:
    def test_hash_is_stable_and_salted(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = CaptureStore(Path(tmp), user_salt="salt-a")
            b = CaptureStore(Path(tmp), user_salt="salt-b")
            assert a.user_hash("u@x.com") == a.user_hash("u@x.com")
            assert a.user_hash("u@x.com") != b.user_hash("u@x.com")

    def test_hash_does_not_contain_raw_email(self, store):
        assert "alice" not in store.user_hash("alice@example.com")


class TestAppendAndRead:
    def test_append_then_iter(self, store):
        uh = store.user_hash("a@b.c")
        store.append_turn(_turn(uh))
        records = list(store.iter_records())
        assert len(records) == 1
        assert records[0]["conversation_id"] == "conv"

    def test_iter_date_bounds(self, store):
        uh = store.user_hash("a@b.c")
        store.append_turn(_turn(uh))
        assert list(store.iter_records(start_date="2999-01-01")) == []
        assert list(store.iter_records(end_date="1999-01-01")) == []

    def test_malformed_lines_are_skipped(self, store):
        uh = store.user_hash("a@b.c")
        path = store.append_turn(_turn(uh))
        with open(path, "a", encoding="utf-8") as f:
            f.write("not json\n")
        records = list(store.iter_records())
        assert len(records) == 1


class TestStats:
    def test_stats_counts_pairs_and_labels(self, store):
        uh = store.user_hash("a@b.c")
        store.set_consent("a@b.c", True)
        store.append_turn(_turn(uh))
        store.append_turn(
            _turn(uh, kind="pair", source="rollback",
                  rejected=Trajectory(tool_calls=[{"name": "fetch", "arguments": {}}]))
        )
        stats = store.stats()
        assert stats["total_records"] == 2
        assert stats["preference_pairs"] == 1
        assert stats["by_label_source"]["rollback"] == 1
        assert stats["opted_in_users"] == 1
        assert stats["storage_bytes"] > 0


class TestSelfDelete:
    def test_delete_removes_records_and_consent(self, store):
        store.set_consent("a@b.c", True)
        uh = store.user_hash("a@b.c")
        store.append_turn(_turn(uh))
        removed, files = store.delete_user_data("a@b.c")
        assert removed == 1
        assert list(store.iter_records()) == []
        assert store.get_consent("a@b.c").enabled is False

    def test_delete_only_targets_requesting_user(self, store):
        ua = store.user_hash("a@b.c")
        ub = store.user_hash("b@b.c")
        store.append_turn(_turn(ua))
        store.append_turn(_turn(ub))
        store.delete_user_data("a@b.c")
        remaining = list(store.iter_records())
        assert len(remaining) == 1
        assert remaining[0]["consent"]["user_hash"] == ub


class TestFilenameSafety:
    def test_path_traversal_in_hash_is_neutralized(self, store):
        # A crafted hash must never escape the data directory.
        evil = _turn("../../etc/passwd")
        path = store.append_turn(evil)
        assert path is not None
        assert store.root in path.resolve().parents
