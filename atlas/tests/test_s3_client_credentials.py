"""Tests for S3 credential handling.

boto3 only falls back to its standard credential chain (env vars, shared
config, container credentials, EC2/EKS instance roles) when the explicit
credential kwargs are omitted or ``None``. Empty strings disable the chain,
so the client must normalize falsy values to ``None``.
"""

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def captured_boto(monkeypatch):
    """Stub boto3.client and capture the kwargs it was called with."""
    from atlas.modules.file_storage import s3_client as s3_mod

    monkeypatch.setattr(s3_mod.S3StorageClient, "_ensure_bucket", lambda self: None)
    calls = {}
    fake_client = MagicMock()

    def _fake_boto_client(*args, **kwargs):
        calls["args"] = args
        calls["kwargs"] = kwargs
        return fake_client

    monkeypatch.setattr(s3_mod.boto3, "client", _fake_boto_client)
    return calls


def _build(access_key, secret_key):
    from atlas.modules.file_storage.s3_client import S3StorageClient

    return S3StorageClient(
        s3_endpoint="http://fake:9000",
        s3_bucket_name="b",
        s3_access_key=access_key,
        s3_secret_key=secret_key,
        s3_region="us-east-1",
        s3_timeout=1,
        s3_use_ssl=False,
    )


@pytest.mark.parametrize("empty", ["", None])
def test_no_credentials_leaves_boto_credential_chain_intact(captured_boto, monkeypatch, empty):
    from atlas.modules.config import config_manager

    monkeypatch.setattr(config_manager.app_settings, "s3_access_key", None, raising=False)
    monkeypatch.setattr(config_manager.app_settings, "s3_secret_key", None, raising=False)

    _build(empty, empty)

    kwargs = captured_boto["kwargs"]
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
    # The rest of the client configuration is unaffected.
    assert kwargs["endpoint_url"] == "http://fake:9000"
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["use_ssl"] is False
    assert kwargs["config"].signature_version == "s3v4"


def test_explicit_credentials_are_passed_through(captured_boto):
    _build("minioadmin", "minioadmin-secret")

    kwargs = captured_boto["kwargs"]
    assert kwargs["aws_access_key_id"] == "minioadmin"
    assert kwargs["aws_secret_access_key"] == "minioadmin-secret"


def test_configured_credentials_are_used_when_not_injected(captured_boto, monkeypatch):
    """MinIO deployments setting S3_ACCESS_KEY/S3_SECRET_KEY keep working."""
    from atlas.modules.config import config_manager

    monkeypatch.setattr(config_manager.app_settings, "s3_access_key", "env-key", raising=False)
    monkeypatch.setattr(config_manager.app_settings, "s3_secret_key", "env-secret", raising=False)

    _build(None, None)

    kwargs = captured_boto["kwargs"]
    assert kwargs["aws_access_key_id"] == "env-key"
    assert kwargs["aws_secret_access_key"] == "env-secret"


def test_settings_default_to_no_credentials():
    from atlas.modules.config.settings import AppSettings

    fields = AppSettings.model_fields
    assert fields["s3_access_key"].default is None
    assert fields["s3_secret_key"].default is None


@pytest.mark.parametrize(
    "access_key,secret_key",
    [("only-access", None), ("only-access", ""), (None, "only-secret"), ("", "only-secret")],
)
def test_partial_credentials_fall_back_to_the_chain(captured_boto, monkeypatch, access_key, secret_key):
    """Half a key pair cannot sign a request; ignore it rather than break signing."""
    from atlas.modules.config import config_manager

    monkeypatch.setattr(config_manager.app_settings, "s3_access_key", None, raising=False)
    monkeypatch.setattr(config_manager.app_settings, "s3_secret_key", None, raising=False)

    _build(access_key, secret_key)

    kwargs = captured_boto["kwargs"]
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs
