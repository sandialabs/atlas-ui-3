"""Tests for externally hosted Help documentation (issue #1107).

Covers the three display modes -- ``local``, ``remote_markdown`` and
``external_link`` -- plus the parts that are easy to get wrong:

* relative and absolute image references in a hosted document are resolved
  against the document URL (and never mistaken for ATLAS-local paths),
* the hosted document is cached, refreshed with a conditional request, and
  the last known good copy survives an upstream failure,
* the external-link mode fetches nothing at all,
* a missing or unusable URL is reported as a configuration error instead of
  leaving a Help button that goes nowhere.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config import config_manager
from atlas.modules.help import remote_content
from atlas.modules.help.help_config import (
    HELP_MODE_EXTERNAL_LINK,
    HELP_MODE_LOCAL,
    HELP_MODE_REMOTE_MARKDOWN,
    resolve_help_config,
)

pytestmark = pytest.mark.usefixtures("mock_admin_authorization")

# A 1x1 transparent PNG.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c6300010000050001"
    "0d0a2db40000000049454e44ae426082"
)

MARKDOWN = """# Hosted Help

Some hosted documentation.

![relative](images/logo.png)
![root relative](/help-images/logo.png)
![absolute elsewhere](https://cdn.example.org/other.png)
"""


class _DocsHandler(BaseHTTPRequestHandler):
    """A tiny documentation host: Markdown, one image, and a 304 path."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        server = self.server
        server.requests.append((self.path, dict(self.headers)))

        if server.fail:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if self.path == "/help.md":
            etag = '"doc-v1"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("ETag", etag)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = MARKDOWN.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("ETag", etag)
            self.send_header("Last-Modified", "Wed, 01 Oct 2026 00:00:00 GMT")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/images/logo.png":
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG_BYTES)))
            self.end_headers()
            self.wfile.write(PNG_BYTES)
            return

        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # noqa: D102 - silence the default stderr logging
        pass


class _DocsServer:
    """A documentation host running in a background thread."""

    def __init__(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _DocsHandler)
        self._server.requests = []
        self._server.fail = False
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def requests(self):
        return self._server.requests

    def set_fail(self, fail: bool) -> None:
        self._server.fail = fail

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def docs_server():
    server = _DocsServer()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def clean_help_caches():
    """The document/image caches are process-wide; start every test empty."""
    remote_content.clear_caches()
    yield
    remote_content.clear_caches()


@pytest.fixture
def help_settings(monkeypatch):
    """Point the deployment's Help settings at a test configuration.

    The settings the routes read are the ones on the app factory's config
    manager, so that is what gets patched.
    """
    settings = app_factory.get_config_manager().app_settings

    def configure(mode="local", url="", **overrides):
        monkeypatch.setattr(settings, "help_display_mode", mode)
        monkeypatch.setattr(settings, "help_content_url", url)
        monkeypatch.setattr(settings, "help_allowed_hosts", overrides.get("allowed_hosts", ""))
        monkeypatch.setattr(
            settings, "help_remote_cache_ttl_seconds", overrides.get("ttl", 300)
        )
        monkeypatch.setattr(
            settings, "help_remote_timeout_seconds", overrides.get("timeout", 10.0)
        )
        monkeypatch.setattr(
            settings, "help_remote_max_bytes", overrides.get("max_bytes", 1_048_576)
        )
        monkeypatch.setattr(
            settings,
            "help_remote_max_image_bytes",
            overrides.get("max_image_bytes", 5_242_880),
        )
        return settings

    return configure


def _client():
    return TestClient(app)


def _user_headers():
    return {"X-User-Email": config_manager.app_settings.test_user}


def _admin_headers():
    return {"X-User-Email": config_manager.app_settings.admin_test_user}


# ---------------------------------------------------------------------------
# local mode (the default, unchanged behavior)
# ---------------------------------------------------------------------------

def test_help_defaults_to_local_mode(help_settings):
    """Without configuration the Help page keeps serving the local Markdown."""
    help_settings(mode="local")
    client = _client()
    resp = client.get("/api/help", headers=_user_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["content"].strip()
    assert body["content_url"] == ""
    assert body["link_url"] == ""
    assert body["error"] is None


def test_api_config_reports_help_display_mode(help_settings):
    """/api/config tells the client which Help mode is in effect."""
    help_settings(mode="local")
    client = _client()
    body = client.get("/api/config", headers=_user_headers()).json()
    assert body["help"]["mode"] == HELP_MODE_LOCAL
    assert body["help"]["content_url"] == ""
    # The pre-#1107 field is still there and still the local document.
    assert isinstance(body["help_content"], str)
    assert body["help_content"].strip()


def test_config_shell_reports_help_display_mode(help_settings):
    """The shell config carries the mode too, so the header knows on first paint."""
    help_settings(mode="external_link", url="https://docs.example.org/atlas/")
    client = _client()
    body = client.get("/api/config/shell", headers=_user_headers()).json()
    assert body["help"]["mode"] == HELP_MODE_EXTERNAL_LINK
    assert body["help"]["content_url"] == "https://docs.example.org/atlas/"


# ---------------------------------------------------------------------------
# remote_markdown mode
# ---------------------------------------------------------------------------

def test_remote_markdown_serves_hosted_document(help_settings, docs_server):
    """A hosted document is fetched server-side and returned as Markdown."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    resp = client.get("/api/help", headers=_user_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == HELP_MODE_REMOTE_MARKDOWN
    assert "# Hosted Help" in body["content"]
    assert body["source"] == f"{docs_server.base_url}/help.md"
    assert body["error"] is None
    # The document really was fetched from the host.
    assert any(path == "/help.md" for path, _ in docs_server.requests)


def test_remote_markdown_resolves_relative_images(help_settings, docs_server):
    """Relative image references resolve against the document, not ATLAS."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    content = client.get("/api/help", headers=_user_headers()).json()["content"]

    # A relative reference becomes an absolute URL on the documentation host,
    # served through the Atlas image proxy.
    assert f"/api/help/image?url=http%3A%2F%2F127.0.0.1%3A{docs_server.port}%2Fimages%2Flogo.png" in content
    # A root-relative reference is resolved against the documentation host
    # too -- it is not an ATLAS-local /help-images path.
    assert f"/api/help/image?url=http%3A%2F%2F127.0.0.1%3A{docs_server.port}%2Fhelp-images%2Flogo.png" in content
    # An absolute reference to another host is left alone (the default CSP
    # blocks it; widening the policy is the operator's call).
    assert "https://cdn.example.org/other.png" in content
    assert "cdn.example.org" not in content.split("![absolute elsewhere]")[0]


def test_remote_markdown_image_proxy_serves_the_image(help_settings, docs_server):
    """The rewritten image reference actually resolves to the image bytes."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    content = client.get("/api/help", headers=_user_headers()).json()["content"]
    proxy_path = content.split("![relative](")[1].split(")")[0]
    resp = client.get(proxy_path, headers=_user_headers())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/")
    assert resp.content == PNG_BYTES


def test_image_proxy_refuses_other_hosts(help_settings, docs_server):
    """The image proxy is not an open fetcher."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    resp = client.get(
        "/api/help/image?url=https%3A%2F%2Fevil.example.org%2Fpayload.png",
        headers=_user_headers(),
    )
    assert resp.status_code == 403


def test_image_proxy_rejects_non_images(help_settings, docs_server):
    """A non-image on the documentation host is not served as an image."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    resp = client.get(
        f"/api/help/image?url=http%3A%2F%2F127.0.0.1%3A{docs_server.port}%2Fhelp.md",
        headers=_user_headers(),
    )
    assert resp.status_code == 502


def test_remote_markdown_caches_and_revalidates(help_settings, docs_server):
    """A second request reuses the cached copy; a later one revalidates it."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md", ttl=0)
    client = _client()

    first = client.get("/api/help", headers=_user_headers()).json()
    assert first["cached"] is False
    assert len(docs_server.requests) == 1

    # TTL 0 means every request revalidates. The host answers 304, so the
    # content is reused and no second body is transferred.
    second = client.get("/api/help", headers=_user_headers()).json()
    assert second["cached"] is True
    assert second["content"] == first["content"]
    assert len(docs_server.requests) == 2
    assert docs_server.requests[1][1].get("If-None-Match") == '"doc-v1"'


def test_remote_markdown_within_ttl_does_not_refetch(help_settings, docs_server):
    """A fresh cache entry is served without touching the host again."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md", ttl=300)
    client = _client()

    client.get("/api/help", headers=_user_headers())
    assert len(docs_server.requests) == 1

    second = client.get("/api/help", headers=_user_headers()).json()
    assert second["cached"] is True
    assert len(docs_server.requests) == 1


def test_remote_markdown_serves_stale_copy_when_the_host_fails(help_settings, docs_server):
    """An upstream failure falls back to the last known good document."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md", ttl=0)
    client = _client()

    good = client.get("/api/help", headers=_user_headers()).json()
    assert "# Hosted Help" in good["content"]

    docs_server.set_fail(True)
    degraded = client.get("/api/help", headers=_user_headers()).json()
    assert degraded["stale"] is True
    assert "# Hosted Help" in degraded["content"]
    assert degraded["error"]


def test_remote_markdown_falls_back_to_local_content(help_settings):
    """With no cached copy and an unreachable host, the local document is used."""
    # Port 9 (discard) refuses connections: nothing is listening there.
    help_settings(mode="remote_markdown", url="http://127.0.0.1:9/help.md", timeout=2.0)
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["content"].strip()
    assert body["fallback"] == "local"
    assert body["error"]


# ---------------------------------------------------------------------------
# external_link mode
# ---------------------------------------------------------------------------

def test_external_link_mode_returns_the_link_and_fetches_nothing(
    help_settings, docs_server
):
    """External-link mode never contacts the documentation host."""
    help_settings(mode="external_link", url=f"{docs_server.base_url}/help.md")
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_EXTERNAL_LINK
    assert body["link_url"] == f"{docs_server.base_url}/help.md"
    assert body["content"] == ""
    assert docs_server.requests == []


# ---------------------------------------------------------------------------
# configuration errors
# ---------------------------------------------------------------------------

def test_remote_mode_without_url_is_a_configuration_error(help_settings):
    """A remote mode with no URL is reported and falls back to local."""
    help_settings(mode="remote_markdown", url="")
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["requested_mode"] == HELP_MODE_REMOTE_MARKDOWN
    assert "HELP_CONTENT_URL" in body["error"]
    assert body["content"].strip()


def test_external_link_mode_without_url_is_a_configuration_error(help_settings):
    """The same applies to the external-link mode."""
    help_settings(mode="external_link", url="")
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["link_url"] == ""
    assert "HELP_CONTENT_URL" in body["error"]


def test_invalid_display_mode_is_reported(help_settings):
    """An unknown mode names the valid ones and falls back to local."""
    help_settings(mode="nonsense", url="https://docs.example.org/help.md")
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert "nonsense" in body["error"]
    assert "remote_markdown" in body["error"]


def test_plain_http_url_for_a_remote_host_is_rejected(help_settings):
    """Documentation is fetched over https; a plain http URL is refused."""
    help_settings(mode="remote_markdown", url="http://docs.example.org/help.md")
    config = resolve_help_config(app_factory.get_config_manager().app_settings)
    assert config.mode == HELP_MODE_LOCAL
    assert "https" in config.error


def test_url_with_credentials_is_rejected(help_settings):
    """Credentials in the URL are refused."""
    help_settings(mode="remote_markdown", url="https://user:pass@docs.example.org/help.md")
    config = resolve_help_config(app_factory.get_config_manager().app_settings)
    assert config.mode == HELP_MODE_LOCAL
    assert "credentials" in config.error


def test_image_proxy_without_configuration_is_not_found(help_settings):
    """With no documentation URL there is nothing to proxy."""
    help_settings(mode="local", url="")
    client = _client()
    resp = client.get(
        "/api/help/image?url=https%3A%2F%2Fdocs.example.org%2Flogo.png",
        headers=_user_headers(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Admin UI visibility
# ---------------------------------------------------------------------------

def test_admin_help_config_reports_the_display_mode(help_settings):
    """The admin endpoint says which mode is in effect."""
    help_settings(mode="external_link", url="https://docs.example.org/atlas/")
    client = _client()
    body = client.get("/admin/help-config", headers=_admin_headers()).json()
    assert body["mode"] == HELP_MODE_EXTERNAL_LINK
    assert body["content_url"] == "https://docs.example.org/atlas/"
    assert body["external"] is True


def test_admin_help_config_local_mode_is_not_external(help_settings):
    """In local mode the editor still controls what users see."""
    help_settings(mode="local")
    client = _client()
    body = client.get("/admin/help-config", headers=_admin_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["external"] is False
