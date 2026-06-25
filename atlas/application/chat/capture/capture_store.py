"""Filesystem storage for fine-tune capture: consent + captured turns.

Mirrors the feedback subsystem's "user JSON under a configured dir" pattern.
Layout under the capture root (default ``runtime/finetune_capture``)::

    consent/<user_hash>.json              one consent record per user
    data/<YYYY-MM-DD>/<user_hash>.jsonl   one captured turn per line

``user_hash`` is a salted SHA-256 (see :meth:`user_hash`) so raw emails never
appear in filenames or records. Set ``CAPTURE_USER_SALT`` for real pseudonymity
across deployments; without it a fixed default salt is used so the hash is at
least stable across restarts (lookups and self-delete keep working).
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from atlas.domain.capture.models import CapturedTurn, ConsentRecord

logger = logging.getLogger(__name__)

_DEFAULT_SALT = "atlas-finetune-capture"


def _safe_hash_component(value: str) -> str:
    """Return a filesystem-safe token (defends against path traversal)."""
    return "".join(c for c in value if c.isalnum() or c in ("-", "_"))[:64]


class CaptureStore:
    """JSONL + consent storage for captured turns.

    Stateless apart from the resolved root directory and a write lock; safe to
    instantiate per request. All public methods fail soft: capture is a
    best-effort side channel and must never take down a chat turn.
    """

    def __init__(self, root: Path, user_salt: Optional[str] = None):
        self._root = Path(root)
        self._user_salt = user_salt or _DEFAULT_SALT
        self._write_lock = threading.Lock()

    # ------------------------------------------------------------------ paths
    @property
    def root(self) -> Path:
        return self._root

    def _consent_dir(self) -> Path:
        d = self._root / "consent"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _data_dir(self) -> Path:
        d = self._root / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------- identity
    def user_hash(self, user_email: Optional[str]) -> str:
        """Return the salted, stable pseudonymous hash for ``user_email``."""
        normalized = (user_email or "").strip().lower()
        digest = hashlib.sha256(
            f"{self._user_salt}:{normalized}".encode("utf-8")
        ).hexdigest()
        return digest[:32]

    # -------------------------------------------------------------- consent
    def _consent_path(self, user_hash: str) -> Path:
        return self._consent_dir() / f"{_safe_hash_component(user_hash)}.json"

    def get_consent(self, user_email: Optional[str]) -> ConsentRecord:
        """Load a user's consent record, defaulting to opted-out."""
        user_hash = self.user_hash(user_email)
        path = self._consent_path(user_hash)
        if not path.exists():
            return ConsentRecord(user_hash=user_hash, enabled=False)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return ConsentRecord.from_dict(json.load(f))
        except Exception as exc:
            logger.warning("Failed to read consent for %s: %s", user_hash, exc)
            return ConsentRecord(user_hash=user_hash, enabled=False)

    def set_consent(
        self,
        user_email: Optional[str],
        enabled: bool,
        consent_version: int = 1,
    ) -> ConsentRecord:
        """Persist a user's opt-in decision and return the stored record."""
        user_hash = self.user_hash(user_email)
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_consent(user_email)
        record = ConsentRecord(
            user_hash=user_hash,
            enabled=enabled,
            consent_version=consent_version,
            consented_at=now if enabled else existing.consented_at,
            revoked_at=None if enabled else now,
        )
        path = self._consent_path(user_hash)
        with self._write_lock:
            tmp = path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record.to_dict(), f, indent=2)
            tmp.replace(path)
        logger.info(
            "Capture consent updated user_hash=%s enabled=%s version=%s",
            user_hash,
            enabled,
            consent_version,
        )
        return record

    # ------------------------------------------------------------- writing
    def _turn_path(self, user_hash: str, when: Optional[datetime] = None) -> Path:
        when = when or datetime.now(timezone.utc)
        day = when.strftime("%Y-%m-%d")
        day_dir = self._data_dir() / day
        day_dir.mkdir(parents=True, exist_ok=True)
        return day_dir / f"{_safe_hash_component(user_hash)}.jsonl"

    def append_turn(self, turn: CapturedTurn) -> Optional[Path]:
        """Append one captured turn as a JSONL line. Returns the file path."""
        user_hash = (turn.consent or {}).get("user_hash") or "unknown"
        path = self._turn_path(user_hash)
        line = json.dumps(turn.to_dict(), ensure_ascii=False)
        try:
            with self._write_lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            return path
        except Exception as exc:
            logger.error("Failed to append captured turn: %s", exc, exc_info=True)
            return None

    # ------------------------------------------------------------- reading
    def iter_records(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield raw record dicts across all users, optionally date-bounded.

        ``start_date``/``end_date`` are inclusive ``YYYY-MM-DD`` strings matched
        against the day directory name.
        """
        data_dir = self._data_dir()
        for day_dir in sorted(data_dir.iterdir()) if data_dir.exists() else []:
            if not day_dir.is_dir():
                continue
            day = day_dir.name
            if start_date and day < start_date:
                continue
            if end_date and day > end_date:
                continue
            for jsonl in sorted(day_dir.glob("*.jsonl")):
                try:
                    with open(jsonl, "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                yield json.loads(line)
                            except json.JSONDecodeError:
                                logger.warning("Skipping malformed line in %s", jsonl)
                except Exception as exc:
                    logger.warning("Failed reading %s: %s", jsonl, exc)

    def stats(self) -> Dict[str, Any]:
        """Aggregate counts for the admin dashboard."""
        total = 0
        pairs = 0
        by_label: Dict[str, int] = {}
        users: set = set()
        bytes_on_disk = 0
        data_dir = self._data_dir()
        if data_dir.exists():
            for jsonl in data_dir.rglob("*.jsonl"):
                try:
                    bytes_on_disk += jsonl.stat().st_size
                except OSError:
                    pass
        for rec in self.iter_records():
            total += 1
            if rec.get("kind") == "pair":
                pairs += 1
            source = (rec.get("label") or {}).get("source", "implicit")
            by_label[source] = by_label.get(source, 0) + 1
            user_hash = (rec.get("consent") or {}).get("user_hash")
            if user_hash:
                users.add(user_hash)
        opted_in = 0
        consent_dir = self._root / "consent"
        if consent_dir.exists():
            for cfile in consent_dir.glob("*.json"):
                try:
                    with open(cfile, "r", encoding="utf-8") as f:
                        if json.load(f).get("enabled"):
                            opted_in += 1
                except Exception:
                    continue
        return {
            "total_records": total,
            "preference_pairs": pairs,
            "by_label_source": by_label,
            "contributing_users": len(users),
            "opted_in_users": opted_in,
            "storage_bytes": bytes_on_disk,
        }

    # --------------------------------------------------------- self-delete
    def delete_user_data(self, user_email: Optional[str]) -> Tuple[int, int]:
        """Remove a user's captured turns and consent record.

        Rewrites each day's JSONL file without this user's hash (a per-user
        file is simply removed). Returns ``(records_removed, files_touched)``.
        """
        user_hash = self.user_hash(user_email)
        safe = _safe_hash_component(user_hash)
        removed = 0
        files = 0
        data_dir = self._data_dir()
        if data_dir.exists():
            for jsonl in list(data_dir.rglob(f"{safe}.jsonl")):
                try:
                    with open(jsonl, "r", encoding="utf-8") as f:
                        removed += sum(1 for line in f if line.strip())
                    with self._write_lock:
                        jsonl.unlink()
                    files += 1
                except Exception as exc:
                    logger.warning("Failed deleting %s: %s", jsonl, exc)
        # Defensive sweep: also drop any line carrying this user_hash that may
        # have been written under a different filename.
        if data_dir.exists():
            for jsonl in list(data_dir.rglob("*.jsonl")):
                try:
                    kept: List[str] = []
                    dropped = 0
                    with open(jsonl, "r", encoding="utf-8") as f:
                        for line in f:
                            s = line.strip()
                            if not s:
                                continue
                            try:
                                rec = json.loads(s)
                            except json.JSONDecodeError:
                                kept.append(s)
                                continue
                            if (rec.get("consent") or {}).get("user_hash") == user_hash:
                                dropped += 1
                            else:
                                kept.append(s)
                    if dropped:
                        removed += dropped
                        files += 1
                        with self._write_lock:
                            with open(jsonl, "w", encoding="utf-8") as f:
                                for s in kept:
                                    f.write(s + "\n")
                except Exception as exc:
                    logger.warning("Failed rewriting %s: %s", jsonl, exc)
        consent_path = self._consent_path(user_hash)
        if consent_path.exists():
            try:
                consent_path.unlink()
            except OSError as exc:
                logger.warning("Failed deleting consent %s: %s", consent_path, exc)
        logger.info(
            "Capture self-delete user_hash=%s removed=%d files=%d",
            user_hash,
            removed,
            files,
        )
        return removed, files
