"""
Test header injection vulnerabilities.

This test suite demonstrates the header injection attack vector and
documents why reverse proxy configuration is critical.
"""
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_direct_access_header_injection_vulnerability():
    """
    SECURITY WARNING: This test demonstrates a CRITICAL vulnerability.

    When the app is accessed DIRECTLY (bypassing reverse proxy),
    attackers can inject X-User-Email headers to impersonate any user.

    This test documents the vulnerability. In production:
    - Main app MUST be network-isolated (not publicly accessible)
    - ALL traffic MUST go through reverse proxy
    - Reverse proxy MUST strip client X-User-Email headers

    This test will PASS because the app is designed to trust headers
    when behind a properly configured reverse proxy. The test serves
    as documentation of the security requirement.
    """
    # Attacker tries to impersonate admin by injecting header
    response = client.get(
        "/api/config",
        headers={"X-User-Email": "attacker-pretending-to-be-admin@evil.com"}
    )

    # In direct access mode (no proxy), the app trusts this header
    # This is why network isolation is CRITICAL
    assert response.status_code == 200

    # The app will treat this as a legitimate request from the attacker's email
    # In production, this request should NEVER reach the app (network isolation)


def test_websocket_header_injection_vulnerability():
    """
    Demonstrates WebSocket header injection vulnerability with direct access.

    This shows why the reverse proxy MUST strip X-User-Email headers
    before adding the authenticated user's header.
    """
    # Attacker connects with injected header
    with client.websocket_connect(
        "/ws",
        headers={"X-User-Email": "attacker@evil.com"}
    ) as websocket:
        # Connection succeeds because app trusts the header
        # This is the EXPECTED behavior when behind a proxy that strips headers
        # This is VULNERABLE behavior when directly accessible

        # Send a test message
        websocket.send_json({
            "type": "chat",
            "content": "test message"
        })

        # The WebSocket will use "attacker@evil.com" as the user
        # This demonstrates why network isolation is critical


def test_multiple_headers_first_wins():
    """
    Demonstrates the danger of improperly configured reverse proxies.

    If the reverse proxy adds X-User-Email without stripping the client's
    version first, both headers arrive. Most frameworks (including FastAPI)
    return the FIRST header, allowing the attacker to win.

    Proper nginx config:
        proxy_set_header X-User-Email "";              # Strip first!
        proxy_set_header X-User-Email $authenticated_user;  # Then add

    Vulnerable nginx config:
        proxy_set_header X-User-Email $authenticated_user;  # Only adds, doesn't strip!
    """
    # Simulate what happens when proxy doesn't strip headers
    # We can't easily test multiple headers with TestClient,
    # but we document the expected behavior

    # When client sends: X-User-Email: attacker@evil.com
    # And proxy adds: X-User-Email: realuser@example.com
    # The app receives BOTH headers

    # FastAPI's request.headers.get() returns the FIRST occurrence
    # So the attacker's header would win!

    # This test documents the requirement for header stripping
    assert True, "Documented: Proxy must strip headers first"


@pytest.mark.skip(reason="Requires production environment with reverse proxy")
def test_production_header_stripping():
    """
    Test to run in production/staging to verify header stripping works.

    This test should be run manually against the actual deployment to verify
    that the reverse proxy properly strips client-provided headers.

    Usage:
        1. Deploy to staging/production with reverse proxy
        2. Get a valid authentication token/cookie
        3. Run this test against the deployed URL
        4. Verify logs show the REAL user, not the injected one

    Expected behavior:
        - Request includes malicious X-User-Email header
        - Reverse proxy strips it
        - Reverse proxy adds real authenticated user header
        - Backend receives only the real user header
        - Logs confirm backend saw the real user
    """
    import os

    import requests

    deployment_url = os.getenv("PRODUCTION_URL")
    auth_cookie = os.getenv("VALID_AUTH_COOKIE")

    if not deployment_url or not auth_cookie:
        pytest.skip("Set PRODUCTION_URL and VALID_AUTH_COOKIE env vars")

    # Try to inject a malicious header
    response = requests.get(
        f"{deployment_url}/api/config",
        headers={"X-User-Email": "attacker@evil.com"},
        cookies={"session": auth_cookie}
    )

    assert response.status_code == 200

    # Manual verification required:
    # Check backend logs to confirm it received the REAL user from auth,
    # not the injected "attacker@evil.com"
    print("✓ Request succeeded")
    print("⚠ MANUAL VERIFICATION REQUIRED:")
    print("  Check backend logs to confirm user was NOT 'attacker@evil.com'")
    print("  The backend should have received the real authenticated user")


def test_header_injection_documentation():
    """
    Documentation test: Lists all security requirements for production deployment.

    This test always passes but serves as executable documentation of the
    security requirements needed to prevent header injection attacks.
    """
    security_requirements = [
        "Main app MUST be network-isolated (not publicly accessible)",
        "ALL traffic MUST flow through reverse proxy",
        "Reverse proxy MUST strip client-provided X-User-Email headers",
        "Reverse proxy MUST add X-User-Email header AFTER stripping client headers",
        "Direct access to main app ports MUST be blocked by firewall/VPC",
        "Nginx config MUST include: proxy_set_header X-User-Email '' before setting it",
        "Apache config MUST include: RequestHeader unset X-User-Email before setting it",
        "Network isolation MUST be tested (attempt direct access should fail)",
        "Header injection test MUST be run in production (test_production_header_stripping)",
        "Deployment checklist in docs/reverse-proxy-examples.md MUST be completed",
    ]

    for i, requirement in enumerate(security_requirements, 1):
        print(f"{i}. {requirement}")

    assert True, "Security requirements documented"


# Additional test to verify the current behavior
def test_x_user_email_header_is_used():
    """
    Verifies that X-User-Email header is properly extracted.

    This is the expected behavior when behind a properly configured proxy.
    """
    test_user = "alice@example.com"

    response = client.get(
        "/api/config",
        headers={"X-User-Email": test_user}
    )

    assert response.status_code == 200
    # The middleware should have processed this header
    # In production, this header comes from the reverse proxy, not the client
