#!/usr/bin/env bash
#
# s3-prune-old-files.sh — delete S3 objects older than N days. Cron-friendly.
#
# Designed to run inside the Atlas app container (or a sibling container with
# the same environment), so it reads the same S3_* env vars the main app uses.
# No --env-file needed in that setup.
#
# REQUIRED ENV (already present in the prod container):
#   S3_BUCKET_NAME    Bucket to prune.
#
# OPTIONAL ENV (already present in the prod container):
#   S3_ENDPOINT       Custom endpoint URL (e.g. MinIO: http://minio:9000).
#                     Omit for real AWS S3.
#   S3_ACCESS_KEY     Mapped to AWS_ACCESS_KEY_ID for the aws CLI.
#   S3_SECRET_KEY     Mapped to AWS_SECRET_ACCESS_KEY for the aws CLI.
#   S3_REGION         Mapped to AWS_DEFAULT_REGION (default: us-east-1).
#
# OPTIONAL ENV (script-specific, can be set instead of CLI flags):
#   S3_PRUNE_DAYS     Default retention in days if --days not given.
#   S3_PRUNE_PREFIX   Default key prefix if --prefix not given (e.g. "uploads/").
#
# FLAGS:
#   --days N          Delete objects whose LastModified is older than N days.
#                     Required, unless S3_PRUNE_DAYS is set.
#   --prefix P        Only consider keys under this prefix. Optional.
#   --dry-run         List what would be deleted, but don't delete anything.
#   -h, --help        Show this help.
#
# REQUIREMENTS:
#   - The `aws` CLI must be on PATH inside the container.
#   - Credentials and endpoint come from the env vars above (or from the
#     container's IAM role / instance profile, if no S3_ACCESS_KEY is set).
#
# EXIT CODES:
#   0  success (including dry-run)
#   1  config error or one or more deletes failed
#
# CRON USAGE (inside the prod container, env already populated):
#
#   # Daily at 03:15 UTC, prune everything older than 30 days, log to file.
#   15 3 * * * /app/scripts/s3-prune-old-files.sh --days 30 >> /var/log/s3-prune.log 2>&1
#
#   # Same, but only under a prefix:
#   15 3 * * * /app/scripts/s3-prune-old-files.sh --days 30 --prefix uploads/ >> /var/log/s3-prune.log 2>&1
#
#   # Always test first:
#   /app/scripts/s3-prune-old-files.sh --days 30 --dry-run
#
# NOTES:
#   - Container cron daemons typically run jobs with an empty environment.
#     If you use the host crontab to `docker exec` into the app container,
#     env vars from the container's process environment ARE inherited:
#       15 3 * * * docker exec atlas-app /app/scripts/s3-prune-old-files.sh --days 30
#     If you run cron *inside* the container, make sure the cron job sources
#     the same env the app uses (e.g. via BASH_ENV or by exporting in the
#     cron entry), otherwise S3_BUCKET_NAME etc. will be empty.

set -euo pipefail

DAYS=""
PREFIX=""
DRY_RUN=0

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
show_help() { sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --days) DAYS="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) show_help; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

DAYS="${DAYS:-${S3_PRUNE_DAYS:-}}"
PREFIX="${PREFIX:-${S3_PRUNE_PREFIX:-}}"

[[ -n "${DAYS}" ]] || die "--days (or S3_PRUNE_DAYS) is required. See --help."
[[ "$DAYS" =~ ^[0-9]+$ ]] || die "--days must be a non-negative integer, got: $DAYS"
[[ -n "${S3_BUCKET_NAME:-}" ]] || die "S3_BUCKET_NAME is required (should be set in the container env). See --help."

command -v aws >/dev/null 2>&1 || die "aws CLI not found in PATH"

export AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY:-${AWS_ACCESS_KEY_ID:-}}"
export AWS_SECRET_ACCESS_KEY="${S3_SECRET_KEY:-${AWS_SECRET_ACCESS_KEY:-}}"
export AWS_DEFAULT_REGION="${S3_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

AWS_ARGS=()
[[ -n "${S3_ENDPOINT:-}" ]] && AWS_ARGS+=(--endpoint-url "$S3_ENDPOINT")

CUTOFF_EPOCH=$(date -u -d "$DAYS days ago" +%s)
CUTOFF_HUMAN=$(date -u -d "@$CUTOFF_EPOCH" +%Y-%m-%dT%H:%M:%SZ)

log "bucket=s3://${S3_BUCKET_NAME}/${PREFIX} cutoff=${CUTOFF_HUMAN} dry_run=${DRY_RUN} endpoint=${S3_ENDPOINT:-<aws-default>}"

LIST_ARGS=(s3api list-objects-v2 --bucket "$S3_BUCKET_NAME"
           --output text
           --query 'Contents[].[LastModified,Key]')
[[ -n "$PREFIX" ]] && LIST_ARGS+=(--prefix "$PREFIX")

deleted=0
scanned=0
errors=0

while IFS=$'\t' read -r last_modified key; do
  [[ -z "${key:-}" || "$key" == "None" ]] && continue
  scanned=$((scanned + 1))
  obj_epoch=$(date -u -d "$last_modified" +%s 2>/dev/null) || { log "skip (bad date): $key"; continue; }
  if (( obj_epoch < CUTOFF_EPOCH )); then
    if (( DRY_RUN )); then
      log "DRY-RUN would delete: $key  (modified $last_modified)"
      deleted=$((deleted + 1))
    else
      if aws "${AWS_ARGS[@]}" s3api delete-object --bucket "$S3_BUCKET_NAME" --key "$key" >/dev/null; then
        log "deleted: $key  (modified $last_modified)"
        deleted=$((deleted + 1))
      else
        log "FAILED to delete: $key"
        errors=$((errors + 1))
      fi
    fi
  fi
done < <(aws "${AWS_ARGS[@]}" "${LIST_ARGS[@]}")

log "done. scanned=${scanned} deleted=${deleted} errors=${errors}"
(( errors == 0 ))
