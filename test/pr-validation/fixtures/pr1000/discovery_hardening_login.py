"""End-to-end driver for PR #1000: OIDC discovery hardening (issue #983).

Stands up a minimal, real OIDC provider on loopback whose discovery document
can be switched between variants, then drives the real Atlas app's
``/auth/oidc/login`` route against it:

1. A well-formed ``http://localhost`` issuer with loopback endpoints still
   logs in (the local-development path keeps working) and logs the
   "loopback host" warning.
2. A malformed issuer (``http://[::1``) redirects with
   ``oidc_error=discovery_failed`` instead of an HTTP 500.
3. A discovery document advertising an endpoint on a non-loopback
   ``http://`` host is refused, and the server log names the field and host.
4. A discovery document whose endpoint is relative (no scheme) is refused.

Then it checks the shared loopback helper's per-caller rules through the
public entry points (MCP OAuth endpoint validation).

Nothing inside Atlas is mocked.
"""

import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

FAILURES = []
ISSUER = None
OVERRIDES = {}


def check(label, condition, detail=""):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label} {detail}")
        FAILURES.append(label)


class IdPHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if urlparse(self.path).path != "/.well-known/openid-configuration":
            self.send_response(404)
            self.end_headers()
            return
        document = {
            "issuer": ISSUER,
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            "jwks_uri": f"{ISSUER}/jwks",
            "code_challenge_methods_supported": ["S256"],
        }
        document.update(OVERRIDES)
        body = json.dumps(document).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def main():
    global ISSUER
    idp = HTTPServer(("127.0.0.1", 0), IdPHandler)
    threading.Thread(target=idp.serve_forever, daemon=True).start()
    ISSUER = f"http://localhost:{idp.server_port}"
    print(f"Mock IdP listening; issuer is {ISSUER}")

    os.environ.update({
        "FEATURE_OIDC_AUTH_ENABLED": "true",
        "OIDC_ISSUER": ISSUER,
        "OIDC_CLIENT_ID": "atlas-validation",
        "OIDC_CLIENT_SECRET": "validation-secret",
        "OIDC_SESSION_SECRET": "validation-session-secret-at-least-32-chars",
        "OIDC_REDIRECT_URI": "http://testserver/auth/oidc/callback",
        "DEBUG_MODE": "false",
        "FEATURE_PROXY_SECRET_ENABLED": "true",
        "PROXY_SECRET": "validation-proxy-secret",
        "SKIP_AUTHORIZATION_CHECKS": "false",
    })

    from starlette.testclient import TestClient

    import atlas.main as atlas_main
    from atlas.core.oidc.discovery import clear_metadata_cache

    settings = atlas_main.config.app_settings
    client = TestClient(atlas_main.app, raise_server_exceptions=False)
    capture = Capture()
    logging.getLogger("atlas").addHandler(capture)

    def login(issuer, overrides=None):
        settings.oidc_issuer = issuer
        OVERRIDES.clear()
        OVERRIDES.update(overrides or {})
        clear_metadata_cache()
        capture.messages.clear()
        response = client.get("/auth/oidc/login", follow_redirects=False)
        return response, response.headers.get("location", "")

    print("\n1. http://localhost issuer with loopback endpoints still logs in")
    response, location = login(ISSUER)
    check("Login redirects to the discovered authorization endpoint",
          response.status_code in (302, 307) and location.startswith(f"{ISSUER}/authorize"),
          f"(got {response.status_code} {location[:100]})")
    check("The loopback-issuer warning is logged",
          any("OIDC issuer uses http:// on loopback host 'localhost'" in m
              for m in capture.messages), str(capture.messages))

    print("\n2. A malformed issuer fails as discovery_failed, not a 500")
    response, location = login("http://[::1")
    check("Malformed issuer redirects with oidc_error=discovery_failed",
          response.status_code == 302 and location == "/?oidc_error=discovery_failed",
          f"(got {response.status_code} {location})")
    check("The log says the issuer is not a valid URL",
          any("OIDC issuer is not a valid URL" in m for m in capture.messages),
          str(capture.messages))

    print("\n3. A plaintext endpoint on a remote host is refused")
    response, location = login(ISSUER, {"token_endpoint": "http://idp.example.gov/token"})
    check("Remote http token_endpoint redirects with oidc_error=discovery_failed",
          location == "/?oidc_error=discovery_failed", f"(got {response.status_code} {location})")
    check("The log names the field and the host",
          any("token_endpoint" in m and "'idp.example.gov'" in m for m in capture.messages),
          str(capture.messages))

    print("\n4. A relative endpoint is refused")
    response, location = login(ISSUER, {"jwks_uri": "/jwks"})
    check("Relative jwks_uri redirects with oidc_error=discovery_failed",
          location == "/?oidc_error=discovery_failed", f"(got {response.status_code} {location})")
    check("The log quotes the offending value",
          any("jwks_uri" in m and "'/jwks'" in m for m in capture.messages),
          str(capture.messages))

    print("\n5. Shared loopback helper, per-caller rules")
    from atlas.modules.mcp_tools.mcp_oauth import (
        MCPOAuthError,
        is_loopback_url,
        validate_endpoint_url,
    )

    def refused(url, **kwargs):
        try:
            validate_endpoint_url(url, what="validation", **kwargs)
            return False
        except MCPOAuthError:
            return True

    check("MCP OAuth refuses a *.localhost name from a remote document",
          refused("https://ingress.localhost/token", allow_loopback=False))
    check("MCP OAuth refuses a malformed URL as MCPOAuthError",
          refused("https://[::1/token"))
    check("MCP OAuth still accepts http on 127.0.0.1 for a loopback server",
          not refused("http://127.0.0.1:9000/token"))
    check("A *.localhost MCP server is not treated as loopback",
          is_loopback_url("http://mcp.localhost/mcp") is False)

    idp.shutdown()

    print("\n==========================================")
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("All PR #1000 validation checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
