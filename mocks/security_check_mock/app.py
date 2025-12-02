import os
import random

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Optional

app = FastAPI(title="Mock Security Check Service")


class SecurityCheckRequest(BaseModel):
    content: str
    check_type: str
    username: Optional[str] = None
    message_history: List[Dict[str, str]] = []


class SecurityCheckResponse(BaseModel):
    status: str  # "blocked", "allowed-with-warnings", or "good"
    message: Optional[str] = None
    details: Dict = {}


@app.post("/check", response_model=SecurityCheckResponse)
async def check_content(
    request: SecurityCheckRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Very simple, deterministic mock for local testing.

    Behavior:
    - If no/invalid Authorization header: return allowed-with-warnings.
    - If content contains "block-me": status="blocked".
    - If content contains "warn-me": status="allowed-with-warnings".
    - Otherwise: status="good".
    """

    # Treat missing/invalid auth as warning but not hard failure, to keep tests easy.
    if not authorization or not authorization.startswith("Bearer "):
        return SecurityCheckResponse(
            status="allowed-with-warnings",
            message="Missing or invalid Authorization header in mock server",
            details={"reason": "missing_auth"},
        )

    content_lower = request.content.lower()

    if "block-me" in content_lower:
        return SecurityCheckResponse(
            status="blocked",
            message="Mock server blocked content containing 'block-me'",
            details={"keyword": "block-me", "check_type": request.check_type},
        )

    if "warn-me" in content_lower:
        return SecurityCheckResponse(
            status="allowed-with-warnings",
            message="Mock server warns on content containing 'warn-me'",
            details={"keyword": "warn-me", "check_type": request.check_type},
        )

    return SecurityCheckResponse(status="good")


@app.post("/check2", response_model=SecurityCheckResponse)
async def check_content_probabilistic(
    request: SecurityCheckRequest,
    authorization: Optional[str] = Header(default=None),
):
    """Probabilistic mock endpoint for fuzzier testing.

    Behavior:
    - Same auth handling as `/check` (missing/invalid → allowed-with-warnings).
    - Otherwise, with probability `p` (default 0.2) it returns a
      non-"good" status (blocked or allowed-with-warnings), chosen
      according to a configurable split.

    Config via environment variables:
    - SECURITY_MOCK_FLAG_PROB: overall probability (default 0.2).
    - SECURITY_MOCK_BLOCK_FRACTION: fraction of flagged cases that are
      blocked (0.0–1.0, default 0.5). The rest are warnings.
    """

    # Auth behavior mirrors /check
    if not authorization or not authorization.startswith("Bearer "):
        return SecurityCheckResponse(
            status="allowed-with-warnings",
            message="Missing or invalid Authorization header in mock server",
            details={"reason": "missing_auth"},
        )

    # Read configuration from environment
    try:
        flag_prob = float(os.getenv("SECURITY_MOCK_FLAG_PROB", "0.2"))
    except ValueError:
        flag_prob = 0.2

    try:
        block_fraction = float(os.getenv("SECURITY_MOCK_BLOCK_FRACTION", "0.5"))
    except ValueError:
        block_fraction = 0.5

    flag_prob = max(0.0, min(1.0, flag_prob))
    block_fraction = max(0.0, min(1.0, block_fraction))

    # Decide whether to flag this content at all
    if random.random() >= flag_prob:
        return SecurityCheckResponse(status="good")

    # We are flagging this request; choose blocked vs warned
    if random.random() < block_fraction:
        return SecurityCheckResponse(
            status="blocked",
            message="Probabilistic mock blocked this content",
            details={
                "mode": "probabilistic",
                "check_type": request.check_type,
                "flag_probability": flag_prob,
                "block_fraction": block_fraction,
            },
        )

    return SecurityCheckResponse(
        status="allowed-with-warnings",
        message="Probabilistic mock warned on this content",
        details={
            "mode": "probabilistic",
            "check_type": request.check_type,
            "flag_probability": flag_prob,
            "block_fraction": block_fraction,
        },
    )
