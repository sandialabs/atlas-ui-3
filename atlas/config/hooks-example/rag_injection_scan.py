#!/usr/bin/env python3
"""Example RagResponse hook (GH #713): score retrieved RAG content for
prompt-injection patterns and record the medium/high hits.

This replaces the former ``atlas/core/prompt_risk.py``, which ran the same
heuristics unconditionally inside the MCP RAG path and appended to
``logs/security_high_risk.jsonl``. Nothing consumed that file and the check
never blocked anything, so the core-side version was removed; the scoring
lives on here as operator-installed policy that you enable only if you want it.

Two things to decide before enabling:

* **Where records go.** This writes JSONL to ``$ATLAS_RAG_SCAN_LOG`` (default:
  ``rag_injection_scan.jsonl`` next to this script). Point it at whatever your
  collector actually tails.
* **Whether to block.** As written this only observes: it always exits 0 with
  no decision, so retrieval proceeds unchanged. To turn it into an enforcement
  point, emit a ``modify`` decision replacing ``content``, or a ``deny``, from
  the ``high`` branch below. Note that ``RagResponse`` defaults to fail-open
  (``on_error: allow``), so set ``on_error: deny`` in ``hooks.json`` if a
  crash here should block retrieval rather than let it through unscanned.

The heuristics are cheap pattern matching, not a detector: they miss
paraphrased attacks and fire on innocent text that happens to quote an
instruction. Treat a hit as a lead, not a verdict.
"""
import base64
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# score thresholds -> risk level; tune to your own tolerance for noise
THRESHOLD_LOW, THRESHOLD_MEDIUM, THRESHOLD_HIGH = 30, 50, 80

PATTERNS = {
    "override_instructions": (r"ignore\s+(previous|all|everything|above|prior)", 40),
    "disregard": (r"disregard\s+(previous|all|everything|above|prior)", 40),
    "new_instructions": (r"new\s+instructions?\s*:\s*", 35),
    "system_role": (r"\b(system|assistant|user)\s*:\s*", 30),
    "act_as": (r"act\s+as\s+(if\s+)?you\s+(are|were)", 25),
    "pretend": (r"pretend\s+(to\s+be|you\s+are)", 25),
    "role_change": (r"your?\s+(new\s+)?role\s+(is|now)", 30),
    "forget": (r"forget\s+(everything|all|previous)", 35),
    "override": (r"override\s+(previous|default|system)", 35),
    "jailbreak": (r"(jailbreak|developer\s+mode|god\s+mode)", 45),
}


def _entropy(text):
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    return -sum((c / n) * math.log2(max(c / n, 1e-12)) for c in counts.values())


def _looks_encoded(text):
    clean = re.sub(r"\s", "", text or "")
    if len(clean) > 20 and len(clean) % 4 == 0 and re.fullmatch(r"[A-Za-z0-9+/=]+", clean):
        try:
            base64.b64decode(clean, validate=True)
            return True
        except Exception:
            pass
    if len(clean) > 20 and re.fullmatch(r"(0x)?[0-9a-fA-F]+", clean):
        return True
    if re.search(r"\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}", text or ""):
        return True
    # zero-width and other invisible characters used to smuggle instructions
    return bool(re.search(r"[\u200b-\u200d\ufeff\u2060]", text or ""))


def score(text):
    """Return ``(score, risk_level, triggers)`` for one blob of retrieved text."""
    points = 0
    triggers = []
    lowered = (text or "").lower()

    for name, (pattern, weight) in PATTERNS.items():
        if re.search(pattern, lowered):
            points += weight
            triggers.append(name)

    if _looks_encoded(text):
        points += 30
        triggers.append("encoding_detected")

    delimiters = len(re.findall(r"[#*\-_=]{3,}|[\"']{3,}", text or ""))
    if delimiters >= 3:
        points += 25
        triggers.append("excessive_delimiters")
    elif delimiters >= 1:
        points += 10

    if len(text or "") > 10 and _entropy(text) > 4.5:
        points += 20
        triggers.append("high_entropy")

    if len(text or "") > 20:
        caps = sum(1 for c in text if c.isupper()) / max(1, len(text))
        if caps > 0.3:
            points += 15
            triggers.append("excessive_caps")

    if (text or "").count("\n") > 5 or re.search(r"\s{10,}", text or ""):
        points += 15
        triggers.append("formatting_abuse")

    if re.search(r"(human|user|assistant):\s*\n", lowered):
        points += 25
        triggers.append("fake_conversation")

    if len(text or "") > 50 and re.search(r"<[^>]+>.*</[^>]+>|[{}\[\]]", text or ""):
        points += 20
        triggers.append("structured_injection")

    if len(text or "") > 1000:
        points += 15
        triggers.append("excessive_length")

    if points >= THRESHOLD_HIGH:
        level = "high"
    elif points >= THRESHOLD_MEDIUM:
        level = "medium"
    elif points >= THRESHOLD_LOW:
        level = "low"
    else:
        level = "minimal"
    return points, level, triggers


def main():
    env = json.load(sys.stdin)
    payload = env.get("payload", {})
    content = payload.get("content") or ""

    points, level, triggers = score(content)
    if level in ("medium", "high"):
        log_path = Path(
            os.environ.get("ATLAS_RAG_SCAN_LOG")
            or Path(__file__).resolve().parent / "rag_injection_scan.jsonl"
        )
        snippet = content if len(content) <= 240 else content[:240] + "…"
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "type": "rag_injection_scan",
            "user": payload.get("username"),
            "sources": payload.get("qualified_data_sources"),
            "score": points,
            "risk_level": level,
            "triggers": triggers,
            "snippet": snippet,
        }
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            # An audit record that cannot be written is an operational problem:
            # say so on stderr (which Atlas logs) rather than failing the turn.
            print(f"rag_injection_scan: cannot write {log_path}: {exc}", file=sys.stderr)

    # Observe-only: no decision emitted, retrieval proceeds untouched.
    sys.exit(0)


if __name__ == "__main__":
    main()
