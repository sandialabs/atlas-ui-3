"""Regression tests for ``scripts/s3_prune_old_files.py``.

These cover the failure modes flagged in PR #571 review:
  * silent listing failure (must exit non-zero, not pretend success)
  * missing required arguments (clear error, not stack trace)
  * dry-run cutoff math (no real deletes)
  * partial delete failure (must exit non-zero, but not abort)
  * paginator wiring (objects beyond the first page are honored)
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

# ---------------------------------------------------------------------------
# Load the script as a module without depending on it being importable via
# package layout. ``scripts/`` is not a package; import via importlib. The
# module must be registered in ``sys.modules`` before exec_module so the
# ``@dataclass`` machinery can resolve string annotations.
# ---------------------------------------------------------------------------
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "s3_prune_old_files.py"
)
_MODULE_NAME = "_atlas_test_s3_prune_old_files"


@pytest.fixture(scope="module")
def prune_mod():
    if not _SCRIPT_PATH.is_file():
        pytest.skip(f"prune script not present at {_SCRIPT_PATH}")
    spec = importlib.util.spec_from_file_location(_MODULE_NAME, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_MODULE_NAME, None)
        raise
    return module


@pytest.fixture
def env_bucket(monkeypatch):
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.delenv("S3_PRUNE_DAYS", raising=False)
    monkeypatch.delenv("S3_PRUNE_PREFIX", raising=False)
    yield


def _fake_paginator(pages):
    """Build a paginator-like mock that yields ``pages`` for ``paginate``."""
    paginator = MagicMock()
    paginator.paginate.return_value = iter(pages)
    return paginator


def _make_s3_client(pages=None, list_error=None, delete_side_effect=None):
    s3 = MagicMock()
    if list_error is not None:
        # ``paginate`` is a generator; raise when iterated.
        def _raising_paginate(**_kwargs):
            raise list_error
            yield  # pragma: no cover

        paginator = MagicMock()
        paginator.paginate.side_effect = _raising_paginate
        s3.get_paginator.return_value = paginator
    else:
        s3.get_paginator.return_value = _fake_paginator(pages or [])
    if delete_side_effect is not None:
        s3.delete_object.side_effect = delete_side_effect
    else:
        s3.delete_object.return_value = {}
    return s3


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_requires_days(env_bucket, prune_mod):
    with pytest.raises(SystemExit):
        prune_mod.parse_args([])


def test_parse_args_rejects_negative_days(env_bucket, prune_mod):
    with pytest.raises(SystemExit):
        prune_mod.parse_args(["--days", "-1"])


def test_parse_args_rejects_non_integer_days(env_bucket, prune_mod):
    # argparse raises SystemExit with a clear message for type=int violations.
    with pytest.raises(SystemExit):
        prune_mod.parse_args(["--days", "soon"])


def test_parse_args_dangling_value_flag_does_not_crash(env_bucket, prune_mod):
    """``--days`` with no following value must produce a clean argparse error,
    not a Python traceback (the bash script's ``set -u`` failure mode)."""
    with pytest.raises(SystemExit):
        prune_mod.parse_args(["--days"])
    with pytest.raises(SystemExit):
        prune_mod.parse_args(["--prefix"])


def test_parse_args_requires_bucket(monkeypatch, prune_mod):
    monkeypatch.delenv("S3_BUCKET_NAME", raising=False)
    with pytest.raises(SystemExit):
        prune_mod.parse_args(["--days", "30"])


def test_parse_args_uses_env_defaults(env_bucket, monkeypatch, prune_mod):
    monkeypatch.setenv("S3_PRUNE_DAYS", "14")
    monkeypatch.setenv("S3_PRUNE_PREFIX", "uploads/")
    cfg = prune_mod.parse_args([])
    assert cfg.days == 14
    assert cfg.prefix == "uploads/"
    assert cfg.bucket == "test-bucket"
    assert cfg.dry_run is False


def test_parse_args_cli_overrides_env(env_bucket, monkeypatch, prune_mod):
    monkeypatch.setenv("S3_PRUNE_DAYS", "14")
    cfg = prune_mod.parse_args(["--days", "30", "--dry-run"])
    assert cfg.days == 30
    assert cfg.dry_run is True


# ---------------------------------------------------------------------------
# prune (the destructive path)
# ---------------------------------------------------------------------------


def _cfg(prune_mod, **overrides):
    base = dict(
        bucket="test-bucket",
        days=30,
        prefix="",
        dry_run=False,
        endpoint=None,
        access_key=None,
        secret_key=None,
        region="us-east-1",
        use_ssl=True,
    )
    base.update(overrides)
    return prune_mod.PruneConfig(**base)


def _logger():
    log = logging.getLogger("test-prune")
    log.handlers = []
    log.addHandler(logging.NullHandler())
    return log


def test_list_failure_returns_nonzero_exit(prune_mod, monkeypatch):
    """Critical regression: a failed list MUST NOT produce a clean exit.

    The bash script's process-substitution form lost this signal entirely;
    the Python rewrite must return ``EXIT_LIST_FAILED``.
    """
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: datetime(2026, 6, 1, tzinfo=timezone.utc))
    err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "nope"}}, "ListObjectsV2"
    )
    s3 = _make_s3_client(list_error=err)
    rc = prune_mod.prune(_cfg(prune_mod), s3, _logger())
    assert rc == prune_mod.EXIT_LIST_FAILED
    s3.delete_object.assert_not_called()


def test_dry_run_does_not_delete(prune_mod, monkeypatch):
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: now)
    pages = [
        {
            "Contents": [
                {"Key": "old.txt", "LastModified": now - timedelta(days=60)},
                {"Key": "new.txt", "LastModified": now - timedelta(days=1)},
            ]
        }
    ]
    s3 = _make_s3_client(pages=pages)
    rc = prune_mod.prune(_cfg(prune_mod, dry_run=True), s3, _logger())
    assert rc == prune_mod.EXIT_OK
    s3.delete_object.assert_not_called()


def test_real_run_deletes_only_old_objects(prune_mod, monkeypatch):
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: now)
    pages = [
        {
            "Contents": [
                {"Key": "old.txt", "LastModified": now - timedelta(days=60)},
                {"Key": "new.txt", "LastModified": now - timedelta(days=1)},
            ]
        }
    ]
    s3 = _make_s3_client(pages=pages)
    rc = prune_mod.prune(_cfg(prune_mod), s3, _logger())
    assert rc == prune_mod.EXIT_OK
    s3.delete_object.assert_called_once_with(Bucket="test-bucket", Key="old.txt")


def test_partial_delete_failure_returns_nonzero(prune_mod, monkeypatch):
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: now)
    pages = [
        {
            "Contents": [
                {"Key": "a.txt", "LastModified": now - timedelta(days=60)},
                {"Key": "b.txt", "LastModified": now - timedelta(days=60)},
            ]
        }
    ]
    err = ClientError(
        {"Error": {"Code": "InternalError", "Message": "boom"}}, "DeleteObject"
    )
    # First delete succeeds, second blows up.
    s3 = _make_s3_client(pages=pages, delete_side_effect=[{}, err])
    rc = prune_mod.prune(_cfg(prune_mod), s3, _logger())
    assert rc == prune_mod.EXIT_DELETE_FAILED
    assert s3.delete_object.call_count == 2


def test_paginator_iterates_all_pages(prune_mod, monkeypatch):
    """Objects past the 1000-key implicit limit must still be considered."""
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: now)
    old = now - timedelta(days=60)
    pages = [
        {"Contents": [{"Key": f"page1/{i}", "LastModified": old} for i in range(2)]},
        {"Contents": [{"Key": f"page2/{i}", "LastModified": old} for i in range(3)]},
    ]
    s3 = _make_s3_client(pages=pages)
    rc = prune_mod.prune(_cfg(prune_mod), s3, _logger())
    assert rc == prune_mod.EXIT_OK
    assert s3.delete_object.call_count == 5


def test_empty_bucket_succeeds(prune_mod, monkeypatch):
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: datetime(2026, 6, 1, tzinfo=timezone.utc))
    s3 = _make_s3_client(pages=[{}])  # no Contents key
    rc = prune_mod.prune(_cfg(prune_mod), s3, _logger())
    assert rc == prune_mod.EXIT_OK
    s3.delete_object.assert_not_called()


def test_naive_datetime_treated_as_utc(prune_mod, monkeypatch):
    """boto3 normally returns tz-aware LastModified; defensively handle naive."""
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(prune_mod, "_utc_now", lambda: now)
    naive_old = (now - timedelta(days=60)).replace(tzinfo=None)
    pages = [{"Contents": [{"Key": "k", "LastModified": naive_old}]}]
    s3 = _make_s3_client(pages=pages)
    rc = prune_mod.prune(_cfg(prune_mod), s3, _logger())
    assert rc == prune_mod.EXIT_OK
    s3.delete_object.assert_called_once()


# ---------------------------------------------------------------------------
# main() smoke
# ---------------------------------------------------------------------------


def test_main_help_exits_zero(prune_mod, capsys):
    with pytest.raises(SystemExit) as exc:
        prune_mod.parse_args(["--help"])
    # argparse uses code=0 for --help.
    assert exc.value.code == 0


def test_main_returns_config_error_for_missing_args(env_bucket, prune_mod):
    rc = prune_mod.main([])
    assert rc == prune_mod.EXIT_CONFIG
