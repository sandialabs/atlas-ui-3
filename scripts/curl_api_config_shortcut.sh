#!/usr/bin/env bash
# Shortcut to GET /api/config as a tester user
# Usage: ./scripts/curl_api_config_shortcut.sh
# Env overrides: HOST, PORT, USER_EMAIL, URL, CURL_OPTS, JQ_OPTS

set -eu
# Enable pipefail if supported (bash-specific)
if [ -n "${BASH_VERSION:-}" ]; then
  set -o pipefail
fi

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat <<'USAGE'
Usage: ./scripts/curl_api_config_shortcut.sh [--raw]

Description:
  Calls GET /api/config with X-User-Email header. Pretty-prints with jq if available,
  else falls back to python -m json.tool, else prints raw JSON.

Options:
  --raw        Print raw JSON without pretty-printing.
  -h, --help   Show this help and exit.

Environment variables (optional):
  HOST=localhost         Backend host
  PORT=8000              Backend port
  USER_EMAIL=test@test.com   Value for X-User-Email header
  URL=http://HOST:PORT/api/config  Full URL override (takes precedence)
  CURL_OPTS="-sS --http1.1"     Extra curl options
  JQ_OPTS="-S"                 Extra jq options (e.g., -S to sort keys)
USAGE
  exit 0
fi

HOST="${HOST:-localhost}"
PORT="${PORT:-8000}"
USER_EMAIL="${USER_EMAIL:-test@test.com}"
URL="${URL:-http://${HOST}:${PORT}/api/config}"
CURL_OPTS=${CURL_OPTS:-"-sS --http1.1"}
RAW_OUTPUT=false

if [[ "${1:-}" == "--raw" ]]; then
  RAW_OUTPUT=true
fi

if $RAW_OUTPUT; then
  curl ${CURL_OPTS} -H "X-User-Email: ${USER_EMAIL}" "${URL}"
  exit 0
fi

if command -v jq >/dev/null 2>&1; then
  jq_opts=${JQ_OPTS:-}
  curl ${CURL_OPTS} -H "X-User-Email: ${USER_EMAIL}" "${URL}" | jq ${jq_opts}
elif command -v python3 >/dev/null 2>&1; then
  curl ${CURL_OPTS} -H "X-User-Email: ${USER_EMAIL}" "${URL}" | python3 -m json.tool
else
  echo "Warning: neither jq nor python3 found; printing raw JSON" >&2
  curl ${CURL_OPTS} -H "X-User-Email: ${USER_EMAIL}" "${URL}"
fi
