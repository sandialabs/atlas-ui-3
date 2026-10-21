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
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

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

[![linked image](images/logo.png)](setup.md)

![reference image][logo]

[relative link](install.md) and an [absolute one](https://docs.example.org/setup).

[reference link][install]

[install]: ../setup.md
[logo]: images/logo.png
"""


class _DocsHandler(BaseHTTPRequestHandler):
    """A tiny documentation host: Markdown, one image, and a 304 path."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        server = self.server
        server.requests.append((self.path, dict(self.headers)))
        # The routing ignores the query string, so a test can ask for the
        # same image with different parameters.
        route = urlsplit(self.path).path

        if server.fail:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/redirect-elsewhere":
            # A redirect to a host the deployment did not configure.
            self.send_response(302)
            self.send_header("Location", "http://elsewhere.example.org/help.md")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/redirect-here":
            # A redirect to a path on the same host.
            self.send_response(302)
            self.send_header("Location", "/v2/help.md")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/v2/help.md":
            body = b"# Redirected Help\n\n![logo](images/logo.png)\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/malformed.md":
            body = (
                b"# Malformed references\n\n"
                b"![bad port](https://docs.example.org:abc/x.png)\n"
                b"![out of range](https://docs.example.org:99999/x.png)\n"
                b"![fine](images/logo.png)\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/redirect-bad-port":
            # A redirect to a URL whose port is not a number.
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:abc/help.md")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if route == "/big.md":
            body = b"x" * 4096
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/big.png":
            body = b"\x89PNG" + b"x" * 4096
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if route == "/help.md":
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

        if route in ("/images/logo.png", "/v2/images/logo.png"):
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

    # A relative reference becomes an image of the documentation host, served
    # through the Atlas image proxy under an opaque identifier.
    relative_token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/images/logo.png"
    )
    assert f"/api/help/image?h={relative_token}" in content
    # A root-relative reference is resolved against the documentation host
    # too -- it is not an ATLAS-local /help-images path.
    root_token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/help-images/logo.png"
    )
    assert f"/api/help/image?h={root_token}" in content
    assert "/help-images/logo.png" not in content.replace(
        f"/api/help/image?h={root_token}", ""
    )
    # An absolute reference to another host is left alone (the default CSP
    # blocks it; widening the policy is the operator's call).
    assert "https://cdn.example.org/other.png" in content
    assert "cdn.example.org" not in content.split("![absolute elsewhere]")[0]


def test_relative_links_in_a_hosted_document_point_at_the_documentation(
    help_settings, docs_server
):
    """A relative link in a hosted document must not resolve against ATLAS."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    content = client.get("/api/help", headers=_user_headers()).json()["content"]

    base = f"http://127.0.0.1:{docs_server.port}"
    # An inline relative link becomes an absolute URL on the documentation.
    assert f"[relative link]({base}/install.md)" in content
    # A reference-style definition too.
    assert f"[install]: {base}/setup.md" in content
    # An absolute link is left as it is.
    assert "[absolute one](https://docs.example.org/setup)" in content


def test_a_linked_image_keeps_its_proxy_source(help_settings, docs_server):
    """A linked image is not rewritten twice.

    The image source is already an ATLAS proxy path; the link rewrite must
    not resolve it against the document URL. The link the image sits in is
    rewritten too, though: it points at the documentation, not at ATLAS.
    """
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    content = client.get("/api/help", headers=_user_headers()).json()["content"]
    assert f"{docs_server.base_url}/api/help/image" not in content
    # The relative image, the root-relative one, the linked image and the
    # reference-style image: four proxy references, none of them resolved
    # against the documentation host.
    assert content.count("/api/help/image?h=") == 4
    # The link around the image points at the documentation, not at ATLAS.
    base = f"http://127.0.0.1:{docs_server.port}"
    assert f"]({base}/setup.md)" in content


def test_a_failing_host_is_not_retried_on_every_request(help_settings):
    """A replica that starts during an outage does not retry the host at once."""
    # Port 9 (discard) refuses connections: nothing is listening there.
    help_settings(mode="remote_markdown", url="http://127.0.0.1:9/help.md", timeout=2.0)
    client = _client()
    first = client.get("/api/help", headers=_user_headers()).json()
    assert first["fallback"] == "local"

    started = time.monotonic()
    second = client.get("/api/help", headers=_user_headers()).json()
    elapsed = time.monotonic() - started
    assert second["fallback"] == "local"
    # The second request did not wait for the host to time out again.
    assert elapsed < 1.0


def test_an_image_identifier_that_is_not_one_is_refused(help_settings, docs_server):
    """The proxy refuses an identifier that is not a hash."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    for token in ("not-a-hash", "ZZ" * 16, "0" * 31, "0" * 33):
        resp = client.get(f"/api/help/image?h={token}", headers=_user_headers())
        assert resp.status_code == 404


def test_a_reference_style_image_is_served_through_the_proxy(help_settings, docs_server):
    """The definition an image uses is proxied, like the image itself."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    content = client.get("/api/help", headers=_user_headers()).json()["content"]
    token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/images/logo.png"
    )
    assert f"[logo]: /api/help/image?h={token}" in content


def test_the_image_proxy_is_not_served_in_external_link_mode(help_settings, docs_server):
    """In external-link mode nothing is fetched, so there are no images."""
    help_settings(mode="external_link", url=f"{docs_server.base_url}/help.md")
    client = _client()
    token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/images/logo.png"
    )
    resp = client.get(f"/api/help/image?h={token}", headers=_user_headers())
    assert resp.status_code == 404
    assert docs_server.requests == []


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


def test_image_proxy_refuses_an_image_the_document_does_not_mention(
    help_settings, docs_server
):
    """The proxy serves the images of the document, not arbitrary paths."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    # The document was read, so its images are known.
    client.get("/api/help", headers=_user_headers())

    unknown = remote_content.image_token("https://evil.example.org/payload.png")
    resp = client.get(f"/api/help/image?h={unknown}", headers=_user_headers())
    assert resp.status_code == 404

    # A path on the documentation host that the document does not reference is
    # refused as well.
    unmentioned = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/secret.png"
    )
    resp = client.get(f"/api/help/image?h={unmentioned}", headers=_user_headers())
    assert resp.status_code == 404


def test_image_proxy_uses_the_configured_scheme(help_settings, docs_server):
    """The scheme comes from the configuration, not from the request."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    config = resolve_help_config(app_factory.get_config_manager().app_settings)
    target = remote_content.resolve_allowed_target(
        f"https://127.0.0.1:{docs_server.port}/images/logo.png", config
    )
    assert target == f"http://127.0.0.1:{docs_server.port}/images/logo.png"


def test_a_foreign_host_is_refused_outright(help_settings):
    """A host nobody configured cannot be fetched, whatever the path."""
    help_settings(mode="remote_markdown", url="https://docs.example.org/help.md")
    config = resolve_help_config(app_factory.get_config_manager().app_settings)
    with pytest.raises(remote_content.HelpPolicyError):
        remote_content.resolve_allowed_target(
            "https://evil.example.org/payload.png", config
        )


def test_allowed_hosts_widen_the_image_sources(help_settings, docs_server):
    """An operator can list another host for the images, and only that one."""
    second = _DocsServer()
    try:
        help_settings(
            mode="remote_markdown",
            url=f"{docs_server.base_url}/help.md",
            allowed_hosts=f"127.0.0.1:{second.port}",
        )
        client = _client()
        # The document on the first host references an image on the second
        # one; that image is served, because the operator listed the host.
        remote_content.register_image_targets(
            f"{docs_server.base_url}/help.md",
            {
                remote_content.image_token(
                    f"http://127.0.0.1:{second.port}/images/logo.png"
                ): f"http://127.0.0.1:{second.port}/images/logo.png"
            },
        )
        token = remote_content.image_token(
            f"http://127.0.0.1:{second.port}/images/logo.png"
        )
        allowed = client.get(f"/api/help/image?h={token}", headers=_user_headers())
        assert allowed.status_code == 200

        # An image the document does not reference is not served, whichever
        # host it is on.
        third = _DocsServer()
        try:
            foreign = remote_content.image_token(
                f"http://127.0.0.1:{third.port}/images/logo.png"
            )
            refused = client.get(
                f"/api/help/image?h={foreign}", headers=_user_headers()
            )
            assert refused.status_code == 404
        finally:
            third.stop()

        # And a registered image on a host the deployment did not allow is
        # refused by the policy, before the fetch.
        blocked = remote_content.image_token("https://evil.example.org/logo.png")
        remote_content.register_image_targets(
            f"{docs_server.base_url}/help.md",
            {blocked: "https://evil.example.org/logo.png"},
        )
        policy = client.get(f"/api/help/image?h={blocked}", headers=_user_headers())
        assert policy.status_code == 403
    finally:
        second.stop()


def test_image_proxy_rejects_non_images(help_settings, docs_server):
    """A non-image on the documentation host is not served as an image."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/help.md"
    )
    remote_content.register_image_targets(
        f"{docs_server.base_url}/help.md",
        {token: f"http://127.0.0.1:{docs_server.port}/help.md"},
    )
    resp = client.get(f"/api/help/image?h={token}", headers=_user_headers())
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
    assert degraded["fetch_error"]


def test_remote_markdown_falls_back_to_local_content(help_settings):
    """With no cached copy and an unreachable host, the local document is used."""
    # Port 9 (discard) refuses connections: nothing is listening there.
    help_settings(mode="remote_markdown", url="http://127.0.0.1:9/help.md", timeout=2.0)
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["content"].strip()
    assert body["fallback"] == "local"
    assert body["fetch_error"]
    # An outage is not a configuration problem.
    assert body["error"] is None


# ---------------------------------------------------------------------------
# Fetch hardening
# ---------------------------------------------------------------------------

def test_a_redirect_to_a_foreign_host_is_refused(help_settings, docs_server):
    """A redirect cannot aim the request at another host."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/redirect-elsewhere")
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["fallback"] == "local"
    assert body["fetch_error"]


def test_a_redirect_within_the_host_is_followed(help_settings, docs_server):
    """A redirect on the same host is followed, and the images follow it."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/redirect-here")
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_REMOTE_MARKDOWN
    assert "# Redirected Help" in body["content"]
    # The image reference resolves against the URL the document was read
    # from, not against the configured one.
    v2_token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/v2/images/logo.png"
    )
    assert f"/api/help/image?h={v2_token}" in body["content"]


def test_the_image_registry_is_found_after_a_redirect(help_settings, docs_server):
    """A redirected document does not make every image request refetch it.

    The registry is keyed by the configured URL, so the images of a
    redirected document are found under the same key.
    """
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/redirect-here")
    client = _client()
    content = client.get("/api/help", headers=_user_headers()).json()["content"]
    token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/v2/images/logo.png"
    )
    proxy_path = f"/api/help/image?h={token}"
    assert proxy_path in content

    documents_read = len([r for r in docs_server.requests if r[0] == "/redirect-here"])
    resp = client.get(proxy_path, headers=_user_headers())
    assert resp.status_code == 200
    # The image was served from the registry: the document was not read
    # again.
    assert (
        len([r for r in docs_server.requests if r[0] == "/redirect-here"])
        == documents_read
    )


def test_a_cached_redirect_keeps_the_final_url(help_settings, docs_server):
    """A cached copy still resolves its images against the final URL."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/redirect-here", ttl=300)
    client = _client()
    first = client.get("/api/help", headers=_user_headers()).json()
    assert first["cached"] is False

    second = client.get("/api/help", headers=_user_headers()).json()
    assert second["cached"] is True
    assert second["source"] == f"{docs_server.base_url}/v2/help.md"
    v2_token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/v2/images/logo.png"
    )
    assert f"/api/help/image?h={v2_token}" in second["content"]


def test_malformed_image_references_do_not_break_the_page(help_settings, docs_server):
    """A reference with a port that is not a number is left alone.

    It used to raise out of the rewrite, which turned the whole Help page
    into an error -- and the document is cached, so the error repeated for
    the whole TTL.
    """
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/malformed.md")
    client = _client()
    resp = client.get("/api/help", headers=_user_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == HELP_MODE_REMOTE_MARKDOWN
    # The malformed references are left as they are...
    assert "docs.example.org:abc/x.png" in body["content"]
    assert "docs.example.org:99999/x.png" in body["content"]
    # ... and the sound one is still rewritten.
    assert "/api/help/image?h=" in body["content"]


def test_a_redirect_with_a_bad_port_does_not_break_the_page(help_settings, docs_server):
    """A redirect to a URL that cannot be parsed falls back, it does not fail."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/redirect-bad-port")
    client = _client()
    resp = client.get("/api/help", headers=_user_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["fallback"] == "local"
    assert body["fetch_error"]


def test_a_portless_allowed_host_refuses_another_port(help_settings, docs_server):
    """A host listed without a port is pinned to the default port."""
    help_settings(
        mode="remote_markdown",
        url=f"{docs_server.base_url}/help.md",
        allowed_hosts="cdn.example.org",
    )
    config = resolve_help_config(app_factory.get_config_manager().app_settings)
    # The default port of the scheme is the only one that is allowed.
    assert remote_content._allowed_authority(
        "https://cdn.example.org/x.png", config
    ) == "https://cdn.example.org"
    assert remote_content._allowed_authority(
        "https://cdn.example.org:8443/x.png", config
    ) is None
    # A plain http reference to a portless https host is pinned to 443, not
    # to the port 80 it was written with.
    assert remote_content._allowed_authority(
        "http://cdn.example.org/x.png", config
    ) == "https://cdn.example.org"


def test_a_document_over_the_size_cap_is_refused(help_settings, docs_server):
    help_settings(
        mode="remote_markdown",
        url=f"{docs_server.base_url}/big.md",
        max_bytes=100,
    )
    client = _client()
    body = client.get("/api/help", headers=_user_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert body["fallback"] == "local"
    assert "byte limit" in body["fetch_error"]


def test_an_image_over_the_size_cap_is_refused(help_settings, docs_server):
    """An image larger than the configured cap is not served."""
    help_settings(
        mode="remote_markdown",
        url=f"{docs_server.base_url}/help.md",
        max_image_bytes=100,
    )
    client = _client()
    token = remote_content.image_token(
        f"http://127.0.0.1:{docs_server.port}/big.png"
    )
    remote_content.register_image_targets(
        f"{docs_server.base_url}/help.md",
        {token: f"http://127.0.0.1:{docs_server.port}/big.png"},
    )
    resp = client.get(f"/api/help/image?h={token}", headers=_user_headers())
    assert resp.status_code == 502
    assert "byte limit" in resp.json()["detail"]


def test_the_image_cache_is_bounded(help_settings, docs_server):
    """The image cache cannot grow without limit."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md")
    client = _client()
    targets = {}
    for index in range(remote_content.MAX_CACHED_IMAGES + 10):
        url = (
            f"http://127.0.0.1:{docs_server.port}/images/logo.png?variant={index}"
        )
        token = remote_content.image_token(url)
        targets[token] = url
    remote_content.register_image_targets(
        f"{docs_server.base_url}/help.md", targets
    )
    for token in targets:
        resp = client.get(f"/api/help/image?h={token}", headers=_user_headers())
        assert resp.status_code == 200
    assert len(remote_content._image_cache) <= remote_content.MAX_CACHED_IMAGES


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
    body = client.get("/api/help", headers=_admin_headers()).json()
    assert body["mode"] == HELP_MODE_LOCAL
    assert "nonsense" in body["error"]
    assert "remote_markdown" in body["error"]


def test_a_failed_refresh_is_not_retried_on_every_request(help_settings, docs_server):
    """An outage does not make every request wait for the host to time out."""
    help_settings(mode="remote_markdown", url=f"{docs_server.base_url}/help.md", ttl=0)
    client = _client()
    client.get("/api/help", headers=_user_headers())
    requests_before = len(docs_server.requests)

    docs_server.set_fail(True)
    degraded = client.get("/api/help", headers=_user_headers()).json()
    assert degraded["stale"] is True
    requests_after_failure = len(docs_server.requests)

    # The next request is served from the copy that is already here, without
    # another attempt at the failing host -- and it still says that this is
    # the last copy that was fetched.
    again = client.get("/api/help", headers=_user_headers()).json()
    assert again["stale"] is True
    assert again["fetch_error"]
    assert "# Hosted Help" in again["content"]
    assert len(docs_server.requests) == requests_after_failure
    assert requests_after_failure > requests_before


def test_a_reader_is_not_shown_the_configuration_details(help_settings):
    """The message that names the settings is for the operator.

    Everybody else gets a neutral notice: the settings an operator has to
    change are not the reader's business.
    """
    help_settings(mode="remote_markdown", url="")
    client = _client()
    reader_body = client.get(
        "/api/help", headers={"X-User-Email": "reader@example.com"}
    ).json()
    assert reader_body["mode"] == HELP_MODE_LOCAL
    assert "HELP_CONTENT_URL" not in reader_body["error"]
    assert "administrator" in reader_body["error"]

    # The config endpoints carry the mode for the header, but the
    # configuration error is for the operator only there as well.
    config_body = client.get(
        "/api/config", headers={"X-User-Email": "reader@example.com"}
    ).json()
    assert config_body["help"]["mode"] == HELP_MODE_LOCAL
    assert config_body["help"]["error"] is None

    shell_body = client.get(
        "/api/config/shell", headers={"X-User-Email": "reader@example.com"}
    ).json()
    assert shell_body["help"]["error"] is None

    # An administrator gets the detail everywhere.
    admin_config = client.get(
        "/api/config", headers=_admin_headers()
    ).json()
    assert "HELP_CONTENT_URL" in admin_config["help"]["error"]


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
        "/api/help/image?h=0123456789abcdef0123456789abcdef",
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
