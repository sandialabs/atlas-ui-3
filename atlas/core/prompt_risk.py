"""
Prompt injection risk heuristics and structured logging.

Scope: lightweight, configurable thresholds; used for user input and RAG results.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _get_thresholds() -> Dict[str, int]:
    """Get prompt injection risk thresholds from config manager."""
    try:
        from atlas.modules.config import config_manager
        settings = config_manager.app_settings
        return {
            "low": settings.pi_threshold_low,
            "medium": settings.pi_threshold_medium,
            "high": settings.pi_threshold_high,
        }
    except Exception:
        # Fallback to defaults if config not available
        return {
            "low": 30,
            "medium": 50,
            "high": 80,
        }


def calculate_prompt_injection_risk(message: str, *, mode: str = "general") -> Dict[str, object]:
    """
    Calculate a heuristic risk score for prompt injection attempts.

    Returns: { 'score': int, 'risk_level': str, 'triggers': list[str] }
    """
    score = 0
    triggers: List[str] = []

    msg_lower = (message or "").lower()

    # 1) Suspicious patterns
    patterns = {
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
    for name, (pat, pts) in patterns.items():
        if re.search(pat, msg_lower):
            score += pts
            triggers.append(name)

    # 2) Encodings/obfuscation
    if _detect_encoding(message):
        score += 30
        triggers.append("encoding_detected")

    # 3) Statistical anomalies
    # Delimiter density (triple quotes, fences, etc.)
    delimiters = len(re.findall(r"[#*\-_=]{3,}|[\"']{3,}", message or ""))
    if delimiters >= 3:
        score += 25
        triggers.append("excessive_delimiters")
    elif delimiters >= 1:
        score += 10

    # High entropy (possible encoded blob)
    if len(message or "") > 10:
        ent = _calculate_entropy(message)
        if ent > 4.5:
            score += 20
            triggers.append("high_entropy")

    # Excessive caps
    if len(message or "") > 20:
        caps_ratio = sum(1 for c in (message or "") if c.isupper()) / max(1, len(message or ""))
        if caps_ratio > 0.3:
            score += 15
            triggers.append("excessive_caps")

    # 4) Context-breaking attempts
    if (message or "").count("\n") > 5 or re.search(r"\s{10,}", message or ""):
        score += 15
        triggers.append("formatting_abuse")

    if re.search(r"(human|user|assistant):\s*\n", msg_lower):
        score += 25
        triggers.append("fake_conversation")

    if (len(message or "") > 50) and re.search(r"<[^>]+>.*</[^>]+>|[{}\[\]]", message or ""):
        score += 20
        triggers.append("structured_injection")

    # 5) Length penalty
    if len(message or "") > 1000:
        score += 15
        triggers.append("excessive_length")

    # Context-aware normalization (reduce false positives)
    if mode in ("code", "logs"):
        # Code/logs: braces, fences common; soften penalties
        if "excessive_delimiters" in triggers:
            score -= 10
        if "structured_injection" in triggers:
            score -= 10
        score = max(0, score)

    # Risk buckets - get thresholds from config
    thresholds = _get_thresholds()
    if score >= thresholds["high"]:
        level = "high"
    elif score >= thresholds["medium"]:
        level = "medium"
    elif score >= thresholds["low"]:
        level = "low"
    else:
        level = "minimal"

    return {"score": int(score), "risk_level": level, "triggers": triggers}


def _detect_encoding(text: str) -> bool:
    clean = re.sub(r"\s", "", text or "")
    # Base64-like
    if len(clean) > 20 and len(clean) % 4 == 0 and re.match(r"^[A-Za-z0-9+/=]+$", clean or ""):
        try:
            base64.b64decode(clean, validate=True)
            return True
        except Exception:
            pass
    # Hex
    if re.match(r"^(0x)?[0-9a-fA-F]+$", clean or "") and len(clean) > 20:
        return True
    # Escape sequences
    if re.search(r"\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}", text or ""):
        return True
    # Zero-width/unusual unicode
    if re.search(r"[\u200B-\u200D\uFEFF\u2060]", text or ""):
        return True
    return False


def _calculate_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts = Counter(text)
    n = len(text)
    ent = 0.0
    for c in counts.values():
        p = c / n
        ent -= p * math.log2(max(p, 1e-12))
    return ent


def log_high_risk_event(*, source: str, user: Optional[str], content: str, score: int, risk_level: str, triggers: List[str], extra: Optional[Dict[str, object]] = None) -> None:
    """Append a JSONL record for medium/high events to logs/security_high_risk.jsonl."""
    try:
        # Only log medium/high
        if risk_level not in ("medium", "high"):
            return
        base_dir = Path(__file__).resolve().parents[2]
        log_path = base_dir / "logs" / "security_high_risk.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat() + "Z",
            "type": "prompt_risk",
            "source": source,
            "user": user,
            "score": score,
            "risk_level": risk_level,
            "triggers": triggers,
        }
        if extra:
            record.update(extra)
        # include a small snippet only
        snippet = (content or "")
        if len(snippet) > 240:
            snippet = snippet[:240] + "…"
        record["snippet"] = snippet
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Failed to write high risk log: %s", e)
