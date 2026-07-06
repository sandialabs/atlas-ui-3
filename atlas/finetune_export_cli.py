"""CLI to export opt-in fine-tune capture data into training formats.

Reads the JSONL capture store (see ``atlas/application/chat/capture``) and emits
one of three formats:

    --format dpo  -> {"prompt", "tools", "chosen", "rejected"}  (preference pairs;
                     drops records that have no ``rejected`` side)
    --format sft  -> {"prompt", "tools", "completion"}          (from ``chosen`` only)
    --format raw  -> the stored records, unchanged

The on-disk store format is stable, so downstream training pipelines can evolve
independently of how Atlas records turns.

Usage (installed):
    atlas-finetune-export --format dpo -o pairs.jsonl
    atlas-finetune-export --format sft --start-date 2026-05-01
    atlas-finetune-export --format raw --capture-dir /data/finetune_capture

Usage (from source):
    python -m atlas.finetune_export_cli --format dpo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, TextIO

from atlas.application.chat.capture.capture_store import CaptureStore


def _resolve_capture_dir(explicit: Optional[str]) -> Path:
    """Resolve the capture root: flag > RUNTIME_CAPTURE_DIR > project default."""
    if explicit:
        return Path(explicit).expanduser()
    env_dir = os.environ.get("RUNTIME_CAPTURE_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    # atlas/finetune_export_cli.py -> repo root
    project_root = Path(__file__).resolve().parents[1]
    return project_root / "runtime" / "finetune_capture"


def _prompt_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuild the model-facing prompt (system + prefix messages)."""
    context = record.get("context") or {}
    messages = []
    system_prompt = context.get("system_prompt")
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(context.get("messages_prefix") or [])
    return messages


def _to_sft(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    chosen = record.get("chosen")
    if not chosen:
        return None
    return {
        "prompt": _prompt_from_record(record),
        "tools": (record.get("context") or {}).get("available_tools") or [],
        "completion": chosen,
    }


def _to_dpo(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    chosen = record.get("chosen")
    rejected = record.get("rejected")
    if not chosen or not rejected:
        return None
    return {
        "prompt": _prompt_from_record(record),
        "tools": (record.get("context") or {}).get("available_tools") or [],
        "chosen": chosen,
        "rejected": rejected,
    }


def _iter_export(
    store: CaptureStore,
    fmt: str,
    start_date: Optional[str],
    end_date: Optional[str],
) -> Iterator[Dict[str, Any]]:
    for record in store.iter_records(start_date=start_date, end_date=end_date):
        if fmt == "raw":
            yield record
        elif fmt == "sft":
            row = _to_sft(record)
            if row is not None:
                yield row
        elif fmt == "dpo":
            row = _to_dpo(record)
            if row is not None:
                yield row


def _write(rows: Iterator[Dict[str, Any]], out: TextIO) -> int:
    count = 0
    for row in rows:
        out.write(json.dumps(row, ensure_ascii=False) + "\n")
        count += 1
    return count


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export opt-in fine-tune capture data into training formats."
    )
    parser.add_argument(
        "--format",
        choices=["dpo", "sft", "raw"],
        default="raw",
        help="Output format (default: raw).",
    )
    parser.add_argument(
        "--capture-dir",
        default=None,
        help="Capture store root (default: RUNTIME_CAPTURE_DIR or runtime/finetune_capture).",
    )
    parser.add_argument("--start-date", default=None, help="Inclusive YYYY-MM-DD lower bound.")
    parser.add_argument("--end-date", default=None, help="Inclusive YYYY-MM-DD upper bound.")
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output file path (default: stdout).",
    )
    args = parser.parse_args(argv)

    capture_dir = _resolve_capture_dir(args.capture_dir)
    if not capture_dir.exists():
        print(f"No capture data found at {capture_dir}", file=sys.stderr)
        return 1

    store = CaptureStore(capture_dir)
    rows = _iter_export(store, args.format, args.start_date, args.end_date)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            count = _write(rows, f)
        print(f"Wrote {count} {args.format} record(s) to {args.output}", file=sys.stderr)
    else:
        count = _write(rows, sys.stdout)
        print(f"Wrote {count} {args.format} record(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
