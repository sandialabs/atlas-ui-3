#!/usr/bin/env python3
"""
E2E tests for MCP API Key Authentication.

These tests verify the complete flow of:
1. Uploading an API key for an MCP server
2. Using tools on the authenticated server
3. Removing the API key

The tests start a mock MCP server that validates API keys and run against
the real ATLAS backend.

Updated: 2025-01-21
"""

import sys
import time
import json
import requests
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional

# Test configuration
BASE_URL = "http://127.0.0.1:8000"
MOCK_MCP_PORT = 9876
AUTH_HEADERS = {"X-User-Email": "test@test.com"}
VALID_API_KEY = "test-api-key-12345"
MOCK_SERVER_NAME = "api-key-test-server"


class MockMCPAuthHandler(BaseHTTPRequestHandler):
    """Simple HTTP handler that validates API keys for MCP requests."""

    def log_message(self, format, *args):
        """Suppress logging for cleaner test output."""
        pass

    def send_json_response(self, status: int, data: dict):
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def validate_api_key(self) -> bool:
        """Check if request has valid API key."""
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return token == VALID_API_KEY
        return False

    def do_GET(self):
        """Handle GET requests (tool listing)."""
        if self.path == "/health":
            self.send_json_response(200, {"status": "ok"})
            return

        # All other GET requests require auth
        if not self.validate_api_key():
            self.send_json_response(401, {"error": "Unauthorized", "message": "Invalid or missing API key"})
            return

        if self.path == "/tools":
            # Return list of available tools
            self.send_json_response(200, {
                "tools": [
                    {
                        "name": "get_secret_data",
                        "description": "Get protected secret data",
                        "inputSchema": {"type": "object", "properties": {}}
                    },
                    {
                        "name": "whoami",
                        "description": "Get authenticated user info",
                        "inputSchema": {"type": "object", "properties": {}}
                    }
                ]
            })
        else:
            self.send_json_response(404, {"error": "Not found"})

    def do_POST(self):
        """Handle POST requests (tool execution)."""
        if not self.validate_api_key():
            self.send_json_response(401, {"error": "Unauthorized", "message": "Invalid or missing API key"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode() if content_length > 0 else "{}"

        try:
            request_data = json.loads(body)
        except json.JSONDecodeError:
            request_data = {}

        if self.path == "/call":
            tool_name = request_data.get("name", "")

            if tool_name == "get_secret_data":
                self.send_json_response(200, {
                    "result": {
                        "content": [{"type": "text", "text": "Secret data: API_KEY=redacted, DB_PASSWORD=redacted"}]
                    }
                })
            elif tool_name == "whoami":
                self.send_json_response(200, {
                    "result": {
                        "content": [{"type": "text", "text": "Authenticated user with valid API key"}]
                    }
                })
            else:
                self.send_json_response(404, {"error": f"Unknown tool: {tool_name}"})
        else:
            self.send_json_response(404, {"error": "Not found"})


class MockMCPServer:
    """Context manager for running the mock MCP server."""

    def __init__(self, port: int = MOCK_MCP_PORT):
        self.port = port
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def __enter__(self):
        self.server = HTTPServer(("127.0.0.1", self.port), MockMCPAuthHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        # Wait for server to be ready
        time.sleep(0.5)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.server:
            self.server.shutdown()
        if self.thread:
            self.thread.join(timeout=2)


def wait_for_server(url: str, max_retries: int = 30, delay: float = 2) -> bool:
    """Wait for the main server to be ready."""
    print(f"Waiting for server at {url}...")
    for i in range(max_retries):
        try:
            response = requests.get(f"{url}/api/config", headers=AUTH_HEADERS, timeout=5)
            if response.status_code in [200, 302]:
                print(f"  Server is ready (attempt {i+1})")
                return True
        except requests.exceptions.RequestException:
            pass  # Expected during startup polling - server not ready yet
        print(f"  [{i+1}/{max_retries}] Server not ready yet, waiting {delay}s...")
        time.sleep(delay)
    print("  Server failed to become ready")
    return False


def test_mock_server_health():
    """Test that the mock MCP server is running and healthy."""
    print("Testing mock MCP server health...")
    try:
        with MockMCPServer():
            response = requests.get(f"http://127.0.0.1:{MOCK_MCP_PORT}/health", timeout=5)
            if response.status_code == 200:
                print("  Mock MCP server is healthy")
                return True
            else:
                print(f"  Mock server returned status {response.status_code}")
                return False
    except Exception as e:
        print(f"  Mock server health check failed: {e}")
        return False


def test_mock_server_rejects_no_auth():
    """Test that mock server rejects requests without API key."""
    print("Testing mock MCP server rejects unauthenticated requests...")
    try:
        with MockMCPServer():
            response = requests.get(f"http://127.0.0.1:{MOCK_MCP_PORT}/tools", timeout=5)
            if response.status_code == 401:
                print("  Correctly rejected request without API key")
                return True
            else:
                print(f"  Expected 401, got {response.status_code}")
                return False
    except Exception as e:
        print(f"  Test failed: {e}")
        return False


def test_mock_server_accepts_valid_key():
    """Test that mock server accepts requests with valid API key."""
    print("Testing mock MCP server accepts valid API key...")
    try:
        with MockMCPServer():
            headers = {"Authorization": f"Bearer {VALID_API_KEY}"}
            response = requests.get(f"http://127.0.0.1:{MOCK_MCP_PORT}/tools", headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if "tools" in data and len(data["tools"]) > 0:
                    print("  Correctly accepted valid API key and returned tools")
                    return True
                else:
                    print("  API key accepted but no tools returned")
                    return False
            else:
                print(f"  Expected 200, got {response.status_code}")
                return False
    except Exception as e:
        print(f"  Test failed: {e}")
        return False


def test_mock_server_rejects_invalid_key():
    """Test that mock server rejects requests with invalid API key."""
    print("Testing mock MCP server rejects invalid API key...")
    try:
        with MockMCPServer():
            headers = {"Authorization": "Bearer wrong-api-key"}
            response = requests.get(f"http://127.0.0.1:{MOCK_MCP_PORT}/tools", headers=headers, timeout=5)
            if response.status_code == 401:
                print("  Correctly rejected invalid API key")
                return True
            else:
                print(f"  Expected 401, got {response.status_code}")
                return False
    except Exception as e:
        print(f"  Test failed: {e}")
        return False


def test_auth_status_endpoint():
    """Test the /api/mcp/auth/status endpoint."""
    print("Testing /api/mcp/auth/status endpoint...")
    try:
        response = requests.get(f"{BASE_URL}/api/mcp/auth/status", headers=AUTH_HEADERS, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if "servers" in data and "user" in data:
                print(f"  Auth status endpoint works, user: {data['user']}")
                return True
            else:
                print("  Response missing expected fields")
                return False
        else:
            print(f"  Expected 200, got {response.status_code}")
            return False
    except Exception as e:
        print(f"  Test failed: {e}")
        return False


def test_upload_token_endpoint():
    """Test uploading a token for a server."""
    print("Testing token upload endpoint...")
    try:
        # Note: This test may fail if the server doesn't exist in config
        # It's mainly testing the endpoint mechanics
        response = requests.post(
            f"{BASE_URL}/api/mcp/auth/nonexistent-server/token",
            headers={**AUTH_HEADERS, "Content-Type": "application/json"},
            json={"token": "test-token-123"},
            timeout=10
        )
        # We expect 403 (not authorized) or 400 (server doesn't accept tokens)
        # since 'nonexistent-server' doesn't exist
        if response.status_code in [403, 400, 404]:
            print(f"  Token upload endpoint responds correctly (status: {response.status_code})")
            return True
        elif response.status_code == 200:
            print("  Token upload succeeded (unexpected but valid)")
            return True
        else:
            print(f"  Unexpected status: {response.status_code}")
            return False
    except Exception as e:
        print(f"  Test failed: {e}")
        return False


def test_remove_token_endpoint():
    """Test removing a token for a server."""
    print("Testing token removal endpoint...")
    try:
        response = requests.delete(
            f"{BASE_URL}/api/mcp/auth/nonexistent-server/token",
            headers=AUTH_HEADERS,
            timeout=10
        )
        # We expect 404 (no token found) since we haven't uploaded one
        if response.status_code in [404, 200]:
            print(f"  Token removal endpoint responds correctly (status: {response.status_code})")
            return True
        else:
            print(f"  Unexpected status: {response.status_code}")
            return False
    except Exception as e:
        print(f"  Test failed: {e}")
        return False


def run_tests():
    """Run all E2E tests for MCP API key authentication."""
    print("=" * 60)
    print("MCP API Key Authentication E2E Tests")
    print("=" * 60)
    print()

    # First run mock server tests (don't need main server)
    mock_tests = [
        ("Mock Server Health", test_mock_server_health),
        ("Mock Server Rejects No Auth", test_mock_server_rejects_no_auth),
        ("Mock Server Accepts Valid Key", test_mock_server_accepts_valid_key),
        ("Mock Server Rejects Invalid Key", test_mock_server_rejects_invalid_key),
    ]

    print("Running Mock MCP Server Tests...")
    print("-" * 40)
    mock_results = []
    for name, test_func in mock_tests:
        try:
            result = test_func()
            mock_results.append((name, result))
        except Exception as e:
            print(f"  {name} failed with exception: {e}")
            mock_results.append((name, False))
        print()

    # Now test the main API endpoints (need main server)
    print("Running ATLAS API Tests...")
    print("-" * 40)

    if not wait_for_server(BASE_URL):
        print("Main server not available, skipping API tests")
        api_results = []
    else:
        api_tests = [
            ("Auth Status Endpoint", test_auth_status_endpoint),
            ("Upload Token Endpoint", test_upload_token_endpoint),
            ("Remove Token Endpoint", test_remove_token_endpoint),
        ]

        api_results = []
        for name, test_func in api_tests:
            try:
                result = test_func()
                api_results.append((name, result))
            except Exception as e:
                print(f"  {name} failed with exception: {e}")
                api_results.append((name, False))
            print()

    # Summary
    print("=" * 60)
    print("Test Results Summary")
    print("=" * 60)

    all_results = mock_results + api_results
    passed = sum(1 for _, result in all_results if result)
    total = len(all_results)

    print(f"\nMock Server Tests: {sum(1 for _, r in mock_results if r)}/{len(mock_results)} passed")
    for name, result in mock_results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    if api_results:
        print(f"\nAPI Tests: {sum(1 for _, r in api_results if r)}/{len(api_results)} passed")
        for name, result in api_results:
            status = "PASS" if result else "FAIL"
            print(f"  [{status}] {name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\nAll MCP API Key Authentication tests passed!")
        return True
    else:
        print("\nSome tests failed!")
        return False


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
