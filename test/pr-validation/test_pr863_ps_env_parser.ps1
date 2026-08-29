#!/usr/bin/env powershell
<#
.SYNOPSIS
    Regression test for the .env parser in ps_agent_start.ps1 (PR #863).

.DESCRIPTION
    Exercises Resolve-DotEnvValue and Import-DotEnv against the actual
    functions in ps_agent_start.ps1. The script dot-sources a temporary copy
    of ps_agent_start.ps1 with the Main invocation neutralized (so loading it
    does not start any services), then runs assertions.

    Requires PowerShell (pwsh). Run from any directory:

        pwsh -NoProfile -File test/pr-validation/test_pr863_ps_env_parser.ps1

    This is a PowerShell test (the parser under test is PowerShell), so it is
    not invoked by the bash run_pr_validation.sh runner; run it manually with
    pwsh, which is cross-platform.
#>

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$AtlasRoot = (Resolve-Path "$PSScriptRoot/../..").Path
$ScriptPath = Join-Path $AtlasRoot "ps_agent_start.ps1"

if (-not (Test-Path $ScriptPath)) {
    Write-Host "FAIL: ps_agent_start.ps1 not found at $ScriptPath"
    exit 1
}

# Build a temporary copy of the script with the Main invocation neutralized so
# dot-sourcing loads only the function definitions and top-level variables.
$src = Get-Content -Raw $ScriptPath
$patched = $src -replace '(?m)^    Main$', '    # Main neutralized for parser test'
$tmpCopy = Join-Path ([System.IO.Path]::GetTempPath()) "ps_agent_start_test_$PID.ps1"
try {
    Set-Content -Path $tmpCopy -Value $patched -Encoding UTF8
    . $tmpCopy
} catch {
    Write-Host "FAIL: could not dot-source ps_agent_start.ps1: $_"
    Remove-Item $tmpCopy -ErrorAction SilentlyContinue
    exit 1
}

$fail = 0
function Check([string]$Name, [string]$Actual, [string]$Expected) {
    if ($Actual -eq $Expected) {
        Write-Host "PASS: $Name -> [$Actual]"
    } else {
        Write-Host "FAIL: $Name -> got [$Actual], expected [$Expected]"
        $script:fail++
    }
}

# ---------------------------------------------------------------------------
# Resolve-DotEnvValue: quoted values, comments, concatenation, empty
# ---------------------------------------------------------------------------
Check "plain"            (Resolve-DotEnvValue "hello")              "hello"
Check "plain-trim"       (Resolve-DotEnvValue "  hello  ")          "hello"
Check "empty"            (Resolve-DotEnvValue "")                   ""
Check "dq"               (Resolve-DotEnvValue '"value with spaces"') "value with spaces"
Check "sq"               (Resolve-DotEnvValue "'single quoted'")    "single quoted"
Check "dq-equals"        (Resolve-DotEnvValue '"a=b=c"')           "a=b=c"
Check "dq-concat-hash"   (Resolve-DotEnvValue '"bar"#baz')          "bar#baz"
Check "dq-then-comment"  (Resolve-DotEnvValue '"value" # comment') "value"
Check "unquoted-comment"  (Resolve-DotEnvValue "value # comment")   "value"
Check "unquoted-hash-nospace" (Resolve-DotEnvValue "value#nocomment") "value#nocomment"
Check "comment-only"     (Resolve-DotEnvValue " # set later")       ""
Check "leading-hash"     (Resolve-DotEnvValue "#whole thing")       ""
Check "url-with-equals"  (Resolve-DotEnvValue "postgresql://u:p@h/db?x=1") "postgresql://u:p@h/db?x=1"
Check "unquoted-trailing" (Resolve-DotEnvValue "abc ")             "abc"
Check "dq-trailing-space" (Resolve-DotEnvValue '"value "')        "value "
Check "sq-trailing-space" (Resolve-DotEnvValue "'trailing '")      "trailing "
Check "sq-with-hash"     (Resolve-DotEnvValue "'a#b'")              "a#b"

# ---------------------------------------------------------------------------
# Import-DotEnv: end-to-end through a realistic .env file
# ---------------------------------------------------------------------------
$testEnv = Join-Path ([System.IO.Path]::GetTempPath()) "pr863-dotenv-$PID.env"
@"
# comment line
KEY_PLAIN=plain_value
KEY_DQ="double quoted value"
KEY_SQ='single quoted value'
KEY_EQ=url?a=b&c=d
KEY_COMMENT=keep # drop this
KEY_EXPORT=exported
export KEY_EXPLICIT=explicit_export
KEY_EMPTY=
KEY_HASH=val#nospace
KEY_NUM=123
KEY_COMMENT_ONLY= # set later
KEY_CONCAT="bar"#baz
"@ | Set-Content $testEnv -Encoding UTF8

Import-DotEnv -Path $testEnv
Check "import-plain"         ([string]$env:KEY_PLAIN)      "plain_value"
Check "import-dq"            ([string]$env:KEY_DQ)         "double quoted value"
Check "import-sq"            ([string]$env:KEY_SQ)         "single quoted value"
Check "import-eq"            ([string]$env:KEY_EQ)         "url?a=b&c=d"
Check "import-comment"       ([string]$env:KEY_COMMENT)    "keep"
Check "import-export-prefix" ([string]$env:KEY_EXPORT)     "exported"
Check "import-explicit-export" ([string]$env:KEY_EXPLICIT) "explicit_export"
Check "import-empty"         ([string]$env:KEY_EMPTY)      ""
Check "import-hash"          ([string]$env:KEY_HASH)       "val#nospace"
Check "import-num"           ([string]$env:KEY_NUM)        "123"
Check "import-comment-only"  ([string]$env:KEY_COMMENT_ONLY) ""
Check "import-concat"        ([string]$env:KEY_CONCAT)    "bar#baz"

# Cleanup test env vars so they don't leak into the shell that ran the test.
foreach ($k in @("KEY_PLAIN","KEY_DQ","KEY_SQ","KEY_EQ","KEY_COMMENT","KEY_EXPORT","KEY_EXPLICIT","KEY_EMPTY","KEY_HASH","KEY_NUM","KEY_COMMENT_ONLY","KEY_CONCAT")) {
    Remove-Item "Env:$k" -ErrorAction SilentlyContinue
}

Remove-Item $testEnv -ErrorAction SilentlyContinue
Remove-Item $tmpCopy -ErrorAction SilentlyContinue

if ($fail -eq 0) {
    Write-Host "ALL PARSER REGRESSION TESTS PASSED"
    exit 0
} else {
    Write-Host "$fail TEST(S) FAILED"
    exit 1
}