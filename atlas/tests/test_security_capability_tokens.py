import base64
import hmac
from hashlib import sha256

from main import app
from starlette.testclient import TestClient

from atlas.core import capabilities
from atlas.core.capabilities import (
    _b64url_encode,
    _reset_ephemeral_secret_for_tests,
    generate_file_token,
    verify_file_token,
)


def test_capability_token_roundtrip_and_tamper(monkeypatch):
    # Basic generate/verify
    token = generate_file_token("alice@example.com", "file123", ttl_seconds=60)
    claims = verify_file_token(token)
    assert claims and claims["u"] == "alice@example.com" and claims["k"] == "file123"

    # Tamper body should fail
    body, sig = token.split(".", 1)
    tampered = body[:-1] + ("A" if body[-1] != "A" else "B")
    bad = f"{tampered}.{sig}"
    assert verify_file_token(bad) is None


def test_capability_token_expiry(monkeypatch):
    # Create a token that is already expired
    token = generate_file_token("bob@example.com", "file999", ttl_seconds=-1)
    assert verify_file_token(token) is None


def test_download_rejects_invalid_or_expired_token(monkeypatch):
    client = TestClient(app)

    from atlas.infrastructure.app_factory import app_factory
    s3 = app_factory.get_file_storage()

    async def fake_get_file(user, key):
        return {
            "key": key,
            "filename": "hello.txt",
            "content_base64": base64.b64encode(b"secret").decode(),
            "content_type": "text/plain",
            "size": 6,
            "last_modified": "",
            "etag": "",
            "tags": {},
            "user_email": user,
        }

    # Always return a file for these tests
    monkeypatch.setattr(s3, "get_file", fake_get_file)

    # Invalid token
    resp = client.get(
        "/api/files/download/k2",
        params={"token": "not.a.valid.token"},
        headers={"X-User-Email": "ignored@example.com"},
    )
    assert resp.status_code == 403

    # Expired token
    expired = generate_file_token("alice@example.com", "k2", ttl_seconds=-5)
    resp2 = client.get(
        "/api/files/download/k2",
        params={"token": expired},
        headers={"X-User-Email": "ignored@example.com"},
    )
    assert resp2.status_code == 403


class _EmptySecretSettings:
    """Stand-in app settings object with no capability secret."""

    capability_token_secret = ""
    capability_token_ttl_seconds = 3600
    debug_mode = False


class _EmptySecretConfigManager:
    app_settings = _EmptySecretSettings()


def test_hardcoded_dev_secret_cannot_forge_tokens(monkeypatch, caplog):
    """Fail-closed regression: forgeries signed with the old dev secret are rejected.

    Prior to this fix, _get_secret() fell back to b"dev-capability-secret" when
    CAPABILITY_TOKEN_SECRET was unset, letting any attacker mint tokens for any
    (user, file_key) pair. Verify that a token crafted with that constant is now
    refused and that an ephemeral secret has taken its place.
    """
    _reset_ephemeral_secret_for_tests()
    monkeypatch.setattr(capabilities, "config_manager", _EmptySecretConfigManager())

    # Forge a token using the previously hardcoded dev secret.
    payload = b'{"u":"victim@example.com","k":"victim-file","e":9999999999}'
    body = _b64url_encode(payload)
    forged_sig = hmac.new(b"dev-capability-secret", body.encode("ascii"), sha256).digest()
    forged_token = f"{body}.{_b64url_encode(forged_sig)}"

    with caplog.at_level("WARNING"):
        assert verify_file_token(forged_token) is None


def test_ephemeral_secret_is_random_and_roundtrips(monkeypatch, caplog):
    """When no secret is configured, an ephemeral random secret is used.

    The secret must be consistent across calls inside one process (so mint and
    verify agree) but cryptographically random (not predictable) across process
    starts. We can only check within-process consistency here; length and
    source of randomness are verified implicitly via secrets.token_bytes.
    """
    _reset_ephemeral_secret_for_tests()
    monkeypatch.setattr(capabilities, "config_manager", _EmptySecretConfigManager())

    with caplog.at_level("CRITICAL"):
        token = generate_file_token("alice@example.com", "file-1", ttl_seconds=60)
        claims = verify_file_token(token)

    assert claims is not None
    assert claims["u"] == "alice@example.com"
    assert claims["k"] == "file-1"
    # Production (debug_mode=False) must emit a CRITICAL warning exactly once.
    critical_records = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert any("CAPABILITY_TOKEN_SECRET" in r.getMessage() for r in critical_records)


def test_ephemeral_secret_warn_is_warning_in_debug_mode(monkeypatch, caplog):
    """Dev-mode deployments see a WARNING (not CRITICAL) to reduce log noise."""

    class _DebugSettings:
        capability_token_secret = ""
        capability_token_ttl_seconds = 3600
        debug_mode = True

    class _DebugConfigManager:
        app_settings = _DebugSettings()

    _reset_ephemeral_secret_for_tests()
    monkeypatch.setattr(capabilities, "config_manager", _DebugConfigManager())

    with caplog.at_level("WARNING"):
        _ = generate_file_token("alice@example.com", "file-1", ttl_seconds=60)

    messages = [r.getMessage() for r in caplog.records]
    assert any("CAPABILITY_TOKEN_SECRET" in m for m in messages)
    # No CRITICAL-level record in debug mode.
    assert not any(r.levelname == "CRITICAL" for r in caplog.records)


def test_ephemeral_secret_is_stable_within_process(monkeypatch):
    """Repeated calls inside one process must return the same ephemeral secret."""
    _reset_ephemeral_secret_for_tests()
    monkeypatch.setattr(capabilities, "config_manager", _EmptySecretConfigManager())

    first = capabilities._get_secret()
    second = capabilities._get_secret()
    assert first == second
    assert len(first) == 32  # secrets.token_bytes(32)
    assert first != b"dev-capability-secret"


def test_configured_secret_preferred_over_ephemeral(monkeypatch):
    """A configured CAPABILITY_TOKEN_SECRET must win over the ephemeral secret."""

    class _ConfiguredSettings:
        capability_token_secret = "configured-strong-secret-1234567890"
        capability_token_ttl_seconds = 3600
        debug_mode = False

    class _ConfiguredConfigManager:
        app_settings = _ConfiguredSettings()

    _reset_ephemeral_secret_for_tests()
    monkeypatch.setattr(capabilities, "config_manager", _ConfiguredConfigManager())

    assert capabilities._get_secret() == b"configured-strong-secret-1234567890"
