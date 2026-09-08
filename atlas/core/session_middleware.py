"""FIPS-compatible signed session middleware."""

import hashlib
from typing import Any

from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware as StarletteSessionMiddleware
from starlette.types import ASGIApp


class SessionMiddleware(StarletteSessionMiddleware):
    """Starlette session middleware using SHA-256 instead of SHA-1."""

    def __init__(self, app: ASGIApp, secret_key: str, **kwargs: Any) -> None:
        super().__init__(app, secret_key, **kwargs)
        self.signer = TimestampSigner(str(secret_key), digest_method=hashlib.sha256)
