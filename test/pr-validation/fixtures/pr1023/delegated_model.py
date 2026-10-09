"""End-to-end driver for PR #1023: delegated API keys for static LLM models.

Stands up a minimal OIDC provider (discovery, JWKS, an RS256 ID token, and an
RFC 8693 token-exchange endpoint) and an OpenAI-compatible model endpoint that
records the credential each call carries, then drives the real Atlas app: OIDC
login, LLM calls through LiteLLMCaller (which uses the real LiteLLM SDK over
HTTP), and logout.

Nothing is mocked inside Atlas: the stand-ins are the IdP and the model.
"""

import base64
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label} {detail}")
        FAILURES.append(label)


# -- A minimal OIDC provider ------------------------------------------------

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
)
_numbers = _key.public_key().public_numbers()


def _b64(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


JWKS = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": "test-key",
                  "n": _b64(_numbers.n), "e": _b64(_numbers.e)}]}
ISSUER = None
CLIENT_ID = "atlas-validation"
USER_EMAIL = "validation-user@example.gov"
NONCE = {}
EXCHANGES = []


class IdPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/.well-known/openid-configuration":
            return self._json({
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/jwks",
                "end_session_endpoint": f"{ISSUER}/logout",
                "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token",
                                          "urn:ietf:params:oauth:grant-type:token-exchange"],
            })
        if path == "/jwks":
            return self._json(JWKS)
        self.send_response(404)
        self.end_headers()
        return None

    def do_POST(self):
        if urlparse(self.path).path != "/token":
            self.send_response(404)
            self.end_headers()
            return None
        length = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        if form.get("grant_type") == "urn:ietf:params:oauth:grant-type:token-exchange":
            EXCHANGES.append(form)
            return self._json({
                "access_token": f"exchanged-{len(EXCHANGES)}-for-{form.get('audience', '?')}",
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "token_type": "Bearer", "expires_in": 300,
            })
        now = int(time.time())
        id_token = jwt.encode(
            {"iss": ISSUER, "aud": CLIENT_ID, "sub": "subject-1", "email": USER_EMAIL,
             "iat": now, "exp": now + 600, "nonce": NONCE.get("nonce")},
            _private_pem, algorithm="RS256", headers={"kid": "test-key"},
        )
        return self._json({
            "access_token": "user-access-token", "refresh_token": "user-refresh-token",
            "id_token": id_token, "token_type": "Bearer", "expires_in": 3600,
            "scope": "openid profile email",
        })


# -- An OpenAI-compatible model endpoint --------------------------------------

MODEL_CALLS = []


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        MODEL_CALLS.append(self.headers.get("Authorization", ""))
        body = json.dumps({
            "id": "chatcmpl-validation", "object": "chat.completion", "created": int(time.time()),
            "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hello from the model"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    global ISSUER
    idp = serve(IdPHandler)
    ISSUER = f"http://127.0.0.1:{idp.server_port}"
    model = serve(ModelHandler)
    model_url = f"http://127.0.0.1:{model.server_port}/v1"
    print(f"Mock IdP on {ISSUER}, model endpoint on {model_url}")

    config_dir = Path(tempfile.mkdtemp(prefix="atlas-pr1023-"))
    (config_dir / "llmconfig.yml").write_text(f"""
models:
  delegated-model:
    model_name: openai/test-model
    model_url: {model_url}
    api_key_source: delegated
    delegation:
      audience: llm-gateway
      scope: llm-gateway
  system-model:
    model_name: openai/test-model
    model_url: {model_url}
    api_key: sk-system-key
""")
    (config_dir / "mcp.json").write_text("{}")

    os.environ.update({
        "APP_CONFIG_DIR": str(config_dir),
        "FEATURE_OIDC_AUTH_ENABLED": "true",
        "FEATURE_OIDC_DELEGATION_ENABLED": "true",
        "OIDC_DELEGATION_PROVIDER": "token_exchange",
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": CLIENT_ID,
        "OIDC_CLIENT_SECRET": "validation-secret",
        "OIDC_SESSION_SECRET": "validation-session-secret-at-least-32-chars",
        "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
        "MCP_TOKEN_ENCRYPTION_KEY": "validation-mcp-token-encryption-key-32+",
        "DEBUG_MODE": "false",
        "FEATURE_PROXY_SECRET_ENABLED": "true",
        "PROXY_SECRET": "validation-proxy-secret",
        "SKIP_AUTHORIZATION_CHECKS": "false",
        # A server-wide provider key that must never reach a delegated model.
        "OPENAI_API_KEY": "sk-server-env-key",
    })

    import asyncio

    from starlette.testclient import TestClient

    import atlas.main as atlas_main
    from atlas.domain.errors import LLMAuthenticationError
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    client = TestClient(atlas_main.app)
    messages = [{"role": "user", "content": "hello"}]

    def call(model_name, user=USER_EMAIL):
        return asyncio.run(LiteLLMCaller().call_plain(model_name, messages, user_email=user))

    print("\n1. Sign in")
    response = client.get("/auth/oidc/login?next=/", follow_redirects=False)
    params = parse_qs(urlparse(response.headers.get("location", "")).query)
    NONCE["nonce"] = params.get("nonce", [None])[0]
    client.get(f"/auth/oidc/callback?code=validation-code&state={params.get('state', [''])[0]}",
               follow_redirects=False)
    status = client.get("/api/auth/oidc/status").json()
    check("The user is signed in", (status.get("session") or {}).get("user") == USER_EMAIL, str(status))

    print("\n2. A delegated model is called with a token exchanged for the user")
    reply = call("delegated-model")
    check("The model answered", reply == "hello from the model", repr(reply))
    check("One token exchange, for the configured audience and scope",
          len(EXCHANGES) == 1 and EXCHANGES[0].get("audience") == "llm-gateway"
          and EXCHANGES[0].get("scope") == "llm-gateway", str(EXCHANGES))
    check("The exchange presented the user's sign-in token",
          EXCHANGES and EXCHANGES[0].get("subject_token") == "user-access-token")
    check("The model received the exchanged token",
          MODEL_CALLS == ["Bearer exchanged-1-for-llm-gateway"], str(MODEL_CALLS))

    print("\n3. The token is reused until it nears expiry")
    call("delegated-model")
    check("No second exchange", len(EXCHANGES) == 1, f"(exchanges: {len(EXCHANGES)})")
    check("The model received the same token", MODEL_CALLS[-1] == "Bearer exchanged-1-for-llm-gateway",
          str(MODEL_CALLS))

    print("\n4. A system model keeps its own key and needs no exchange")
    call("system-model")
    check("The system model received its configured key", MODEL_CALLS[-1] == "Bearer sk-system-key",
          str(MODEL_CALLS))
    check("Still one exchange", len(EXCHANGES) == 1)

    print("\n5. A user with no sign-in can't use the delegated model")
    before = len(MODEL_CALLS)
    try:
        call("delegated-model", user="someone-else@example.gov")
        refused = False
    except LLMAuthenticationError:
        refused = True
    check("Refused with an authentication error", refused)
    check("The model was not called", len(MODEL_CALLS) == before, str(MODEL_CALLS[before:]))

    print("\n6. After sign-out the delegated model is refused")
    client.get("/auth/oidc/logout", follow_redirects=False)
    before = len(MODEL_CALLS)
    try:
        call("delegated-model")
        refused = False
    except LLMAuthenticationError:
        refused = True
    check("Refused with an authentication error", refused)
    check("The model was not called", len(MODEL_CALLS) == before, str(MODEL_CALLS[before:]))

    print("\n7. No call ever carried the user's own token or the server's key")
    check("Never the user's sign-in token", all("user-access-token" not in c for c in MODEL_CALLS))
    check("Never the server-wide provider key", all("sk-server-env-key" not in c for c in MODEL_CALLS))

    idp.shutdown()
    model.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #1023 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
