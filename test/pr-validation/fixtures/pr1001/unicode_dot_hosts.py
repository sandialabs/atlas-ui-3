"""Driver for PR #1001: MCP OAuth refuses Unicode-dot spellings of internal hosts.

Builds the same URLs a hostile discovery document would name and checks them
through the public MCP OAuth entry points, comparing with the host httpx will
actually connect to. Also confirms ``testserver`` gets no loopback trust.
"""

import sys

import httpx

from atlas.core.oidc.discovery import OIDCDiscoveryError, _validate_issuer_url
from atlas.modules.mcp_tools.mcp_oauth import MCPOAuthError, is_loopback_url, validate_endpoint_url

FAILURES = []


def check(label, condition, detail=""):
    print(f"{'PASSED' if condition else 'FAILED'}: {label} {detail if not condition else ''}")
    if not condition:
        FAILURES.append(label)


def refused(url, **kwargs):
    try:
        validate_endpoint_url(url, what="validation", **kwargs)
        return False
    except MCPOAuthError:
        return True


for dot in ("。", "．", "｡"):
    for labels in (("169", "254", "169", "254"), ("127", "0", "0", "1"), ("a", "localhost")):
        url = f"https://{dot.join(labels)}/token"
        target = httpx.URL(url).host
        check(f"{url!r} (httpx connects to {target}) refused from a remote document",
              refused(url, allow_loopback=False))

check("Public host with ASCII dots still allowed",
      not refused("https://idp.example.gov/token", allow_loopback=False))
check("Unicode-dot loopback MCP server is recognized as loopback",
      is_loopback_url("http://127。0。0。1:8931/mcp"))
check("testserver is not a loopback MCP server", not is_loopback_url("http://testserver/mcp"))
check("http://testserver endpoint is refused", refused("http://testserver/token"))
try:
    _validate_issuer_url("http://testserver/realms/atlas")
    check("http://testserver OIDC issuer is refused", False)
except OIDCDiscoveryError:
    check("http://testserver OIDC issuer is refused", True)

print("\n==========================================")
if FAILURES:
    print(f"FAILED: {len(FAILURES)} check(s)")
    sys.exit(1)
print("All PR #1001 validation checks passed")
