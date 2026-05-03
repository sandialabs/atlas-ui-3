#!/usr/bin/env python3
"""Prune S3 objects older than N days. Cron-friendly.

Designed to run inside the Atlas app container (or a sibling container with
the same environment), reusing the same ``S3_*`` env vars the main app uses.
Uses ``boto3`` directly (already a runtime dependency of Atlas) so the image
does not need the ``aws`` CLI.

REQUIRED ENV (already present in the prod container):
  S3_BUCKET_NAME    Bucket to prune.

OPTIONAL ENV (already present in the prod container):
  S3_ENDPOINT       Custom endpoint URL (e.g. MinIO: http://minio:9000).
                    Omit for real AWS S3.
  S3_ACCESS_KEY     Mapped to AWS access key for boto3.
  S3_SECRET_KEY     Mapped to AWS secret key for boto3.
  S3_REGION         AWS region (default: us-east-1).
  S3_USE_SSL        "true"/"false" — pass to boto3 use_ssl (default: true).

OPTIONAL ENV (script-specific, can be set instead of CLI flags):
  S3_PRUNE_DAYS     Default retention in days if --days not given.
  S3_PRUNE_PREFIX   Default key prefix if --prefix not given (e.g. "uploads/").

FLAGS:
  --days N          Delete objects whose LastModified is older than N days.
                    Required, unless S3_PRUNE_DAYS is set.
  --prefix P        Only consider keys under this prefix. Optional.
  --dry-run         List what would be deleted, but do not delete anything.
  -h, --help        Show this help.

EXIT CODES:
  0  success (including dry-run with zero matches)
  1  config / argument error
  2  S3 listing failed (no objects were inspected — destructive op aborted)
  3  one or more deletes failed (partial work may have completed)

CRON USAGE (host crontab, env inherited from the running container):

  # Daily at 03:15 UTC, prune anything older than 30 days, log to file.
  15 3 * * * docker exec atlas-app python3 /app/scripts/s3_prune_old_files.py \\
      --days 30 >> /var/log/s3-prune.log 2>&1

  # Same, restricted to a prefix:
  15 3 * * * docker exec atlas-app python3 /app/scripts/s3_prune_old_files.py \\
      --days 30 --prefix uploads/

  # Always test first:
  docker exec atlas-app python3 /app/scripts/s3_prune_old_files.py --days 30 --dry-run

NOTES ON BUCKET FEATURES:
  - This script issues unversioned ``DeleteObject`` calls. On a bucket with
    Versioning enabled, that creates a *delete marker*; the prior versions
    remain billable until a separate lifecycle / versioned-delete pass
    removes them. If the target bucket is versioned and you want space
    reclaimed, prefer an S3 Lifecycle rule instead of this script.
  - Objects under S3 Object Lock (governance/compliance) cannot be removed
    by this script and will be reported as failures.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional, Tuple

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

# Exit codes (kept narrow and stable so cron / monitoring can branch on them).
EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_LIST_FAILED = 2
EXIT_DELETE_FAILED = 3


def _utc_now() -> datetime:
    """Wrappable for tests."""
    return datetime.now(timezone.utc)


def _parse_bool_env(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class PruneConfig:
    bucket: str
    days: int
    prefix: str
    dry_run: bool
    endpoint: Optional[str]
    access_key: Optional[str]
    secret_key: Optional[str]
    region: str
    use_ssl: bool


def parse_args(argv: list[str]) -> PruneConfig:
    parser = argparse.ArgumentParser(
        prog="s3_prune_old_files.py",
        description="Delete S3 objects older than N days (cron-friendly).",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Delete objects whose LastModified is older than N days. "
        "Required unless S3_PRUNE_DAYS is set.",
    )
    parser.add_argument(
        "--prefix",
        default=None,
        help="Only consider keys under this prefix.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log what would be deleted; do not delete anything.",
    )
    args = parser.parse_args(argv)

    # Fall back to env defaults for days/prefix.
    days = args.days
    if days is None:
        env_days = os.environ.get("S3_PRUNE_DAYS", "").strip()
        if env_days:
            try:
                days = int(env_days)
            except ValueError:
                parser.error(
                    f"S3_PRUNE_DAYS must be a non-negative integer, got: {env_days!r}"
                )
    if days is None:
        parser.error("--days (or S3_PRUNE_DAYS) is required.")
    if days < 0:
        parser.error(f"--days must be a non-negative integer, got: {days}")

    prefix = args.prefix if args.prefix is not None else os.environ.get("S3_PRUNE_PREFIX", "")

    bucket = os.environ.get("S3_BUCKET_NAME", "").strip()
    if not bucket:
        parser.error(
            "S3_BUCKET_NAME is required (should be set in the container env)."
        )

    return PruneConfig(
        bucket=bucket,
        days=days,
        prefix=prefix,
        dry_run=args.dry_run,
        endpoint=os.environ.get("S3_ENDPOINT") or None,
        access_key=os.environ.get("S3_ACCESS_KEY") or None,
        secret_key=os.environ.get("S3_SECRET_KEY") or None,
        region=os.environ.get("S3_REGION") or "us-east-1",
        use_ssl=_parse_bool_env(os.environ.get("S3_USE_SSL"), default=True),
    )


def build_s3_client(cfg: PruneConfig):
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
        use_ssl=cfg.use_ssl,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
    )


def iter_old_objects(
    s3_client,
    bucket: str,
    prefix: str,
    cutoff: datetime,
) -> Iterator[Tuple[str, datetime]]:
    """Yield (key, last_modified) for objects strictly older than ``cutoff``.

    Uses the boto3 paginator so buckets with >1000 objects are handled.
    Listing errors propagate to the caller as ``ClientError`` /
    ``BotoCoreError`` and must be treated as fatal — silently continuing
    would let cron report success on a destructive job that ran zero
    iterations because the bucket was unreachable.
    """
    paginator = s3_client.get_paginator("list_objects_v2")
    kwargs = {"Bucket": bucket}
    if prefix:
        kwargs["Prefix"] = prefix
    for page in paginator.paginate(**kwargs):
        for obj in page.get("Contents", []) or []:
            last_modified = obj.get("LastModified")
            key = obj.get("Key")
            if key is None or last_modified is None:
                continue
            # boto3 returns timezone-aware datetimes for LastModified.
            if last_modified.tzinfo is None:
                last_modified = last_modified.replace(tzinfo=timezone.utc)
            if last_modified < cutoff:
                yield key, last_modified


def prune(cfg: PruneConfig, s3_client, log: logging.Logger) -> int:
    cutoff = _utc_now() - timedelta(days=cfg.days)
    log.info(
        "bucket=s3://%s/%s cutoff=%s dry_run=%s endpoint=%s",
        cfg.bucket,
        cfg.prefix,
        cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        cfg.dry_run,
        cfg.endpoint or "<aws-default>",
    )

    deleted = 0
    errors = 0
    candidates = iter_old_objects(s3_client, cfg.bucket, cfg.prefix, cutoff)
    while True:
        try:
            key, last_modified = next(candidates)
        except StopIteration:
            break
        except (ClientError, BotoCoreError) as exc:
            # Listing failed (auth, network, permissions). Bail loudly with a
            # distinct exit code so cron / monitoring can branch on it; do
            # NOT keep going as that would mask the failure.
            log.error(
                "FAILED to list bucket s3://%s/%s: %s",
                cfg.bucket,
                cfg.prefix,
                exc,
            )
            return EXIT_LIST_FAILED
        modified_str = last_modified.strftime("%Y-%m-%dT%H:%M:%SZ")
        if cfg.dry_run:
            log.info("DRY-RUN would delete: %s  (modified %s)", key, modified_str)
            deleted += 1
            continue
        try:
            s3_client.delete_object(Bucket=cfg.bucket, Key=key)
            log.info("deleted: %s  (modified %s)", key, modified_str)
            deleted += 1
        except (ClientError, BotoCoreError) as exc:
            log.error("FAILED to delete %s: %s", key, exc)
            errors += 1

    log.info("done. deleted=%d errors=%d", deleted, errors)
    if errors:
        return EXIT_DELETE_FAILED
    return EXIT_OK


def _configure_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
        stream=sys.stdout,
    )
    # Force log timestamps to UTC.
    logging.Formatter.converter = lambda *_args: datetime.now(timezone.utc).timetuple()
    # Quiet boto's chatter at INFO; we only want our own lines.
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("s3-prune")


def main(argv: Optional[list[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    log = _configure_logging()
    try:
        cfg = parse_args(argv)
    except SystemExit as exc:
        # argparse calls sys.exit(2) on error; normalize to EXIT_CONFIG so
        # cron monitors only have to know our documented codes.
        code = exc.code if isinstance(exc.code, int) else EXIT_CONFIG
        return EXIT_CONFIG if code != 0 else EXIT_OK
    s3_client = build_s3_client(cfg)
    return prune(cfg, s3_client, log)


if __name__ == "__main__":
    sys.exit(main())
