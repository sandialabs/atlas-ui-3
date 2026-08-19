#!/usr/bin/env python3
"""Example RagResponse hook (GH #713): score retrieved RAG content for
prompt-injection patterns and record the medium/high hits.

This replaces the former ``atlas/core/prompt_risk.py``, which ran the same
heuristics unconditionally inside the MCP RAG path and appended to
``logs/security_high_risk.jsonl``. Nothing consumed that file and the check
never blocked anything, so the core-side version was removed; the scoring
lives on here as operator-installed policy that you enable only if you want it.

**Where records go.** Pass the destination as the first argv element in
``hooks.json`` -- argv *is* interpolated, so ``${ATLAS_PROJECT_DIR}`` and
``${ATLAS_CONFIG_DIR}`` expand there. Environment variables do **not** work:
``HookManager._build_env`` hands hooks a fixed allow-list (``PATH``, ``HOME``,
``SYSTEMROOT``, ``LANG``, ``LC_ALL``, ``USER``, ``ATLAS_CONFIG_DIR``,
``ATLAS_PROJECT_DIR``) so the server's secrets never leak into a hook, which
means a custom ``$SOMETHING_LOG`` would silently never arrive. Without an argv
path this falls back to ``$ATLAS_PROJECT_DIR/logs/rag_injection_scan.jsonl``.

**Records are sensitive.** Each one carries the requesting ``username``, the
data sources queried, and a 240-character verbatim excerpt of retrieved
document text. Treat the destination as a secret store, the same way
``audit_tool.sh`` warns for tool envelopes.

**Write failures are silent to the operator unless this exits non-zero.**
``HookManager._parse_decision`` discards stderr on exit 0, so an unwritable
path would otherwise read as "no injection observed". This exits 1 in that
case, which is harmless under the example's ``on_error: allow`` (the event
fails open) but shows up in the server log.

**Whether to block.** As written this only observes: with a writable log it
exits 0 with no decision, so retrieval proceeds unchanged. To turn it into an
enforcement point, emit a ``modify`` decision replacing ``content``, or a
``deny``, from the ``high`` branch below. Note that ``RagResponse`` defaults to
fail-open (``on_error: allow``), so set ``on_error: deny`` in ``hooks.json`` if
a crash here should block retrieval rather than let it through unscanned.

**Calibration.** ``RagResponse`` hands over the whole synthesized answer, not
one chunk, so the structural signals (entropy, delimiters, length, formatting)
fire on ordinary long markdown: a benign 900-byte answer scores 60 on them
alone. They therefore cannot escalate on their own -- a ``medium``/``high``
level requires at least one *content* trigger (an instruction-override phrase,
an encoded blob, or a forged conversation turn). Even so these are cheap
pattern matches, not a detector: they miss paraphrased attacks and fire on
innocent text that quotes an instruction. Treat a hit as a lead, not a verdict,
and re-tune before wiring it to ``deny``.
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

# Scoring is regex over attacker-influenced text on the inline retrieval path,
# so it gets a fixed budget rather than however much a document holds.
MAX_SCAN_CHARS = 64_000


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

# Only these say something about *content*. The rest (entropy, delimiters,
# caps, formatting, length, structure) describe shape, and ordinary markdown
# answers trip several of them, so they may add points but never escalate past
# "low" on their own. See "Calibration" in the module docstring.
CONTENT_TRIGGERS = frozenset(PATTERNS) | {"encoding_detected", "fake_conversation"}


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
    # Cap the work: regex over attacker-supplied text, awaited inline on the
    # retrieval path. An injection has to appear early to matter anyway.
    text = (text or "")[:MAX_SCAN_CHARS]
    points = 0
    triggers = []
    lowered = text.lower()

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

    # Bounded quantifiers throughout: the unbounded `<[^>]+>.*</[^>]+>` this
    # replaces backtracked quadratically (60 KB of unclosed `<b>` tags took
    # 1.4s, 120 KB took 5.6s -- past the hook timeout, on the retrieval path).
    if len(text or "") > 50 and re.search(
        r"<[^<>]{1,80}>[^<]{0,500}</[^<>]{1,80}>|[{}\[\]]", text or ""
    ):
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

    # Shape alone does not escalate. Without this, every long markdown answer
    # reports medium: entropy + delimiters + formatting + length sum past the
    # threshold with no injection anywhere in the text.
    if level in ("medium", "high") and not (set(triggers) & CONTENT_TRIGGERS):
        level = "low"
    return points, level, triggers


def log_destination(argv):
    """Where records go: argv[1] if given, else ``$ATLAS_PROJECT_DIR/logs/``.

    Not an env var of its own -- see the module docstring; ``_build_env`` would
    drop it. ``ATLAS_PROJECT_DIR`` is on the allow-list, so the fallback is a
    real log directory rather than this script's own (config) directory, which
    is often read-only, image-baked, or version-controlled.
    """
    if len(argv) > 1 and argv[1].strip():
        return Path(argv[1])
    project_dir = os.environ.get("ATLAS_PROJECT_DIR") or "."
    return Path(project_dir) / "logs" / "rag_injection_scan.jsonl"


def main(argv=None):
    argv = sys.argv if argv is None else argv
    env = json.load(sys.stdin)
    payload = env.get("payload") or {}

    # A preceding RagResponse hook can have replaced `content` with anything
    # (`_apply_modify` merges the payload verbatim), and under the `on_error:
    # deny` the docstring recommends for enforcement, an AttributeError here
    # would block retrieval. Anything that is not text is nothing to scan.
    content = payload.get("content")
    if not isinstance(content, str):
        content = ""

    points, level, triggers = score(content)
    if level not in ("medium", "high"):
        sys.exit(0)

    log_path = log_destination(argv)
    snippet = content if len(content) <= 240 else content[:240] + "\u2026"
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
        # Exit non-zero so this is visible. On exit 0 the manager discards
        # stderr, and a silently unwritable audit path reads exactly like
        # "no injection observed". Under `on_error: allow` (the example
        # config) a non-zero exit still lets the turn through.
        print(f"rag_injection_scan: cannot write {log_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Observe-only: no decision emitted, retrieval proceeds untouched.
    sys.exit(0)


if __name__ == "__main__":
    main()
