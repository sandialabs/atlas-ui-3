"""Synthetic OIDC provider for load tests. NOT production auth.

Adapted from github.com/garland3/atlas-load-test-helper (``mock_oidc.py``) to
what Atlas's relying party actually sends: ``nonce`` is carried into the ID
token, the client may authenticate with HTTP Basic (Atlas's default
``client_secret_basic``) or in the form body, and users carry an ``email``
claim (Atlas's default ``OIDC_USERNAME_CLAIM``).

Real RSA signing and a real JWKS, not a static bearer bypass:

- ``GET /authorize``: Authorization Code + PKCE (S256) with deliberate auto
  login. The load client appends ``login_hint=loaduser-NNNN`` to the URL Atlas
  redirected it to; that name becomes the user.
- ``POST /token``: ``authorization_code``, ``refresh_token`` and RFC 8693
  ``token-exchange`` (what Atlas's ``OIDC_DELEGATION_PROVIDER=token_exchange``
  sends for a model with ``api_key_source: "delegated"``).
- ``GET /stats``: per-grant request counts and failures, read by the load
  runner so the report can show how much IdP traffic a profile caused.

Run: ``MOCK_OIDC_PORT=... OIDC_ISSUER=http://127.0.0.1:<port> python mock_oidc.py``.
Single process only (codes and refresh tokens are in memory).
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from collections import Counter
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

ISSUER = os.environ.get("OIDC_ISSUER", "http://127.0.0.1:8001").rstrip("/")
CLIENT = os.environ.get("OIDC_CLIENT_ID", "atlas-loadtest")
SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
ACCESS_TTL = int(os.environ.get("OIDC_ACCESS_TTL", "900"))
REFRESH_TTL = int(os.environ.get("OIDC_REFRESH_TTL", "3600"))
AUDIENCES = set(os.environ.get("OIDC_ALLOWED_AUDIENCES", "atlas,mock-litellm").split(","))
EMAIL_DOMAIN = os.environ.get("OIDC_EMAIL_DOMAIN", "load.test")
TOKEN_DELAY = float(os.environ.get("OIDC_TOKEN_DELAY_MS", "0")) / 1000.0
# Where /authorize may send the browser back to: Atlas's callback path on
# these hosts only, so the mock is not an open redirect.
REDIRECT_HOSTS = frozenset(h.strip() for h in os.environ.get(
    "OIDC_REDIRECT_HOSTS", "127.0.0.1,localhost,::1").split(",") if h.strip())
CALLBACK_PATH = os.environ.get("OIDC_CALLBACK_PATH", "/auth/oidc/callback")

app = FastAPI(title="Atlas load-test OIDC")
PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_numbers = PRIVATE_KEY.public_key().public_numbers()


def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _uint(value: int) -> str:
    return _b64u(value.to_bytes((value.bit_length() + 7) // 8, "big"))


KID = _b64u(hashlib.sha256(str(_numbers.n).encode()).digest()[:12])
JWK = {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID, "n": _uint(_numbers.n), "e": _uint(_numbers.e)}

CODES: dict = {}
REFRESHES: dict = {}
STATS: Counter = Counter()


def _issue(subject: str, audience: str, scope: str, token_type: str, nonce: str = "") -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "sub": subject, "aud": audience, "iat": now, "nbf": now,
        "exp": now + ACCESS_TTL, "jti": secrets.token_hex(12), "scope": scope,
        "preferred_username": subject, "email": f"{subject}@{EMAIL_DOMAIN}",
        "groups": ["users"], "token_type": token_type,
    }
    if nonce:
        claims["nonce"] = nonce
    return jwt.encode(claims, PRIVATE_KEY, algorithm="RS256", headers={"kid": KID})


def _bundle(subject: str, scope: str, nonce: str = "") -> dict:
    refresh = secrets.token_urlsafe(32)
    REFRESHES[refresh] = (subject, scope, time.time() + REFRESH_TTL)
    return {
        "access_token": _issue(subject, "atlas", scope, "access"),
        "id_token": _issue(subject, CLIENT, scope, "id", nonce),
        "refresh_token": refresh, "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": scope,
    }


def _client_id(request: Request, form: dict) -> str:
    """Accept client_secret_basic, client_secret_post or a public client."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            raw = base64.b64decode(header[6:], validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(401, "invalid_client")
        client_id, _, secret = raw.partition(":")
        client_id, secret = unquote(client_id), unquote(secret)
    else:
        client_id, secret = form.get("client_id", ""), form.get("client_secret", "")
    if client_id != CLIENT or (SECRET and secret != SECRET):
        raise HTTPException(401, "invalid_client")
    return client_id


def _fail(grant: str, status: int, error: str) -> HTTPException:
    STATS[f"{grant}_failed"] += 1
    return HTTPException(status, error)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/stats")
def stats():
    return dict(STATS)


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER, "authorization_endpoint": ISSUER + "/authorize",
        "token_endpoint": ISSUER + "/token", "jwks_uri": ISSUER + "/jwks",
        "response_types_supported": ["code"], "subject_types_supported": ["public"],
        "grant_types_supported": ["authorization_code", "refresh_token",
                                  "urn:ietf:params:oauth:grant-type:token-exchange"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
    }


@app.get("/jwks")
def jwks():
    return {"keys": [JWK]}


@app.get("/authorize")
def authorize(response_type: str, client_id: str, redirect_uri: str, code_challenge: str,
              code_challenge_method: str = "S256", scope: str = "openid", state: str = "",
              nonce: str = "", login_hint: str = ""):
    STATS["authorize"] += 1
    if response_type != "code" or client_id != CLIENT or code_challenge_method != "S256":
        raise _fail("authorize", 400, "invalid_request")
    if not login_hint.startswith("loaduser-"):
        raise _fail("authorize", 400, "synthetic users must start with loaduser-")
    target = urlsplit(redirect_uri)
    if (target.scheme not in ("http", "https") or target.hostname not in REDIRECT_HOSTS
            or target.path != CALLBACK_PATH or target.query or target.fragment):
        raise _fail("authorize", 400, "redirect_uri is not an allowed Atlas callback")
    code = secrets.token_urlsafe(24)
    CODES[code] = (login_hint, redirect_uri, code_challenge, scope, nonce, time.time() + 90)
    location = urlunsplit((target.scheme, target.netloc, CALLBACK_PATH,
                           urlencode({"code": code, "state": state}), ""))
    return RedirectResponse(location, status_code=302)


@app.post("/token")
async def token(request: Request):
    form = dict(await request.form())
    grant = form.get("grant_type", "")
    short = "token_exchange" if grant.endswith("token-exchange") else grant
    STATS[short] += 1
    if TOKEN_DELAY:
        import asyncio

        await asyncio.sleep(TOKEN_DELAY)
    _client_id(request, form)
    if grant == "authorization_code":
        item = CODES.pop(form.get("code", ""), None)
        if not item:
            raise _fail(short, 400, "invalid_grant")
        user, redirect_uri, challenge, scope, nonce, expiry = item
        verifier = form.get("code_verifier", "")
        if (expiry < time.time() or redirect_uri != form.get("redirect_uri")
                or challenge != _b64u(hashlib.sha256(verifier.encode()).digest())):
            raise _fail(short, 400, "invalid_grant")
        return _bundle(user, scope, nonce)
    if grant == "refresh_token":
        item = REFRESHES.pop(form.get("refresh_token", ""), None)
        if not item or item[2] < time.time():
            raise _fail(short, 400, "invalid_grant")
        return _bundle(item[0], item[1])
    if short == "token_exchange":
        if form.get("subject_token_type") != "urn:ietf:params:oauth:token-type:access_token":
            raise _fail(short, 400, "invalid_request")
        try:
            subject = jwt.decode(form.get("subject_token", ""), PRIVATE_KEY.public_key(), algorithms=["RS256"],
                                 issuer=ISSUER, options={"verify_aud": False}, leeway=5)
        except jwt.PyJWTError:
            raise _fail(short, 401, "invalid_token")
        if subject.get("token_type") != "access":
            raise _fail(short, 400, "invalid_grant")
        audience = form.get("audience") or "mock-litellm"
        if audience not in AUDIENCES:
            raise _fail(short, 400, "invalid_target")
        scope = form.get("scope") or subject.get("scope", "openid")
        return {
            "access_token": _issue(subject["sub"], audience, scope, "access"),
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "token_type": "Bearer", "expires_in": ACCESS_TTL, "scope": scope,
        }
    raise _fail(short, 400, "unsupported_grant_type")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["MOCK_OIDC_PORT"]), log_level="warning")
