#!/bin/bash
# Table tests for the launcher URL-host helpers (issue #1052 review).
#
# agent_start.sh and ps_agent_start.ps1 decide from a URL whether a bundled
# service should start; misclassifying an endpoint starts the wrong backend
# or echoes credentials. The helper blocks are extracted from the scripts
# between the "url-host helpers" markers so the tests track the real code.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo "PASSED: $2"
        PASSED=$((PASSED + 1))
    else
        echo "FAILED: $2"
        FAILED=$((FAILED + 1))
    fi
}

extract_helpers() {
    sed -n '/^# url-host helpers start$/,/^# url-host helpers end$/p' "$1"
}

# --- Bash helpers (agent_start.sh) -----------------------------------------
bash_helpers="$(extract_helpers "$PROJECT_ROOT/agent_start.sh")"
if [ -n "$bash_helpers" ]; then
    eval "$bash_helpers"

    while IFS='|' read -r url expected_host expected_local; do
        [ -n "$url" ] || continue
        host="$(url_host "$url")"
        is_local_host "$host"; locality=$?
        if [ "$host" = "$expected_host" ] && [ "$locality" -eq "$expected_local" ]; then
            print_result 0 "bash url_host/is_local_host: '$url' -> host '$host'"
        else
            print_result 1 "bash url_host/is_local_host: '$url' -> host '$host' (wanted '$expected_host', local=$expected_local)"
        fi
    done <<'CASES'
http://localhost:9000|localhost|0
HTTP://LocalHost:9000|LocalHost|0
http://127.0.0.1:9000|127.0.0.1|0
http://127.7.7.7:9000|127.7.7.7|0
http://127.example.com:9000|127.example.com|1
http://[::1]:9000|[::1]|0
http://[2001:db8::1]:9000|[2001:db8::1]|1
http://minio:9000|minio|1
https://s3.example.com|s3.example.com|1
postgresql://atlas:pw@localhost:5432/atlas_chat_history|localhost|0
postgresql://atlas:pw@db.internal:5432/atlas_chat_history|db.internal|1
postgresql://atlas:p@ss@db.internal:5432/x|db.internal|1
postgresql+psycopg://db.internal:5432/x|db.internal|1
postgresql://atlas:pa/ss@localhost/atlas_chat_history||1
duckdb:///data/chat_history.db||1
||1
garbage||1
CASES

    check_redact() { # input expected
        [ "$(redact_url "$1")" = "$2" ]
    }
    check_redact "postgresql://atlas:secret@localhost:5432/db" "postgresql://***@localhost:5432/db"
    print_result $? "bash redact_url hides userinfo"
    check_redact "postgresql://atlas:pa/ss@db.internal/x" "postgresql://***@db.internal/x"
    print_result $? "bash redact_url hides malformed userinfo with a slash"
    check_redact "postgresql://atlas:p?q@s/db" "postgresql://***@s/db"
    print_result $? "bash redact_url hides malformed userinfo with ? and #"
    check_redact "atlas:secretpw@localhost" "***@localhost"
    print_result $? "bash redact_url hides scheme-less userinfo"
    check_redact "http://localhost:9000" "http://localhost:9000"
    print_result $? "bash redact_url leaves URLs without userinfo alone"
else
    echo "FAILED: could not extract bash url-host helpers from agent_start.sh"
    FAILED=$((FAILED + 1))
fi

# --- PowerShell helpers (ps_agent_start.ps1) --------------------------------
if command -v pwsh >/dev/null 2>&1; then
    ps_helpers="$(extract_helpers "$PROJECT_ROOT/ps_agent_start.ps1")"
    if [ -n "$ps_helpers" ]; then
        test_ps="$(mktemp --suffix=.ps1)"
        {
            printf '%s\n' "$ps_helpers"
            cat <<'PSCASES'
$cases = @(
    @("http://localhost:9000", "localhost", 0),
    @("HTTP://LocalHost:9000", "LocalHost", 0),
    @("http://127.0.0.1:9000", "127.0.0.1", 0),
    @("http://127.7.7.7:9000", "127.7.7.7", 0),
    @("http://127.example.com:9000", "127.example.com", 1),
    @("http://[::1]:9000", "[::1]", 0),
    @("http://[2001:db8::1]:9000", "[2001:db8::1]", 1),
    @("http://minio:9000", "minio", 1),
    @("https://s3.example.com", "s3.example.com", 1),
    @("postgresql://atlas:pw@localhost:5432/atlas_chat_history", "localhost", 0),
    @("postgresql://atlas:pw@db.internal:5432/atlas_chat_history", "db.internal", 1),
    @("postgresql://atlas:p@ss@db.internal:5432/x", "db.internal", 1),
    @("postgresql+psycopg://db.internal:5432/x", "db.internal", 1),
    @("postgresql://atlas:pa/ss@localhost/atlas_chat_history", "", 1),
    @("duckdb:///data/chat_history.db", "", 1),
    @("", "", 1),
    @("garbage", "", 1)
)
$failed = 0
foreach ($case in $cases) {
    $url = $case[0]; $expectedHost = $case[1]; $expectedLocal = $case[2]
    $host_ = Get-UrlHost $url
    $local = 1
    if (Test-LocalHost $host_) { $local = 0 }
    if ($host_ -ne $expectedHost -or $local -ne $expectedLocal) {
        Write-Output "FAILED: '$url' -> host '$host_' (wanted '$expectedHost', local=$expectedLocal)"
        $failed = 1
    }
}
if ((Hide-UrlUserInfo "postgresql://atlas:secret@localhost:5432/db") -ne "postgresql://***@localhost:5432/db") { $failed = 1 }
if ((Hide-UrlUserInfo "postgresql://atlas:pa/ss@db.internal/x") -ne "postgresql://***@db.internal/x") { $failed = 1 }
if ((Hide-UrlUserInfo "postgresql://atlas:p?q@s/db") -ne "postgresql://***@s/db") { $failed = 1 }
if ((Hide-UrlUserInfo "atlas:secretpw@localhost") -ne "***@localhost") { $failed = 1 }
if ((Hide-UrlUserInfo "http://localhost:9000") -ne "http://localhost:9000") { $failed = 1 }
if ($failed -ne 0) { exit 1 }
Write-Output "PASSED: PowerShell url-host helpers"
exit 0
PSCASES
        } > "$test_ps"
        if out="$(pwsh -NoProfile -File "$test_ps" 2>&1)"; then
            print_result 0 "PowerShell url-host helper table"
        else
            print_result 1 "PowerShell url-host helper table"
            printf '%s\n' "$out"
        fi
        rm -f "$test_ps"
    else
        echo "FAILED: could not extract PowerShell url-host helpers"
        FAILED=$((FAILED + 1))
    fi
else
    echo "SKIP: pwsh not available; PowerShell helper table not run"
fi

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
