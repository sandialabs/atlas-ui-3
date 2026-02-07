#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SRC_DEFAULTS="$PROJECT_ROOT/config/defaults"
SRC_ENV="$PROJECT_ROOT/.env.example"
DEST_DEFAULTS="$PROJECT_ROOT/atlas/config/defaults"
DEST_ENV="$PROJECT_ROOT/atlas/.env.example"

if [ ! -d "$SRC_DEFAULTS" ]; then
  echo "Error: missing source defaults at $SRC_DEFAULTS" >&2
  exit 1
fi
if [ ! -f "$SRC_ENV" ]; then
  echo "Error: missing source env example at $SRC_ENV" >&2
  exit 1
fi

mkdir -p "$DEST_DEFAULTS"

# Sync defaults
rm -rf "$DEST_DEFAULTS"/*
cp -a "$SRC_DEFAULTS"/. "$DEST_DEFAULTS"/

# Sync env example
cp -a "$SRC_ENV" "$DEST_ENV"

echo "Synced package assets:"
echo "  $SRC_DEFAULTS -> $DEST_DEFAULTS"
echo "  $SRC_ENV -> $DEST_ENV"
