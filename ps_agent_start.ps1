#!/usr/bin/env powershell

<#
.SYNOPSIS
    PowerShell equivalent of agent_start.sh for Windows bare metal environment

.DESCRIPTION
    This script starts the application services (backend, frontend, and optionally MCP mock)
    in a Windows environment with PowerShell. It is the feature-parity counterpart of
    agent_start.sh; see that script for the canonical behavior.

.PARAMETER FrontendOnly
    Only rebuild frontend

.PARAMETER BackendOnly
    Only start backend

.PARAMETER StartMcpMock
    Start MCP mock server

.PARAMETER EnvFile
    Path to .env file to load (default: $ATLAS_ENV_FILE or <project>/.env)

.EXAMPLE
    .\ps_agent_start.ps1
    Start all services using the default .env file.

.EXAMPLE
    .\ps_agent_start.ps1 -e C:\Users\me\.atlasrc -m
    Start all services using a custom env file and the MCP mock server.
#>

param()

# Force UTF-8 output encoding for the console and all output streams on Windows
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Store the project root directory (resolved once at script scope so helper
# functions can reuse it without consulting their own $MyInvocation).
$SCRIPT_PATH = $MyInvocation.MyCommand.Definition
$PROJECT_ROOT = Split-Path -Parent $SCRIPT_PATH
Set-Location $PROJECT_ROOT

# Global variables
$MCP_PID = $null
$UVICORN_PID = $null
$ONLY_FRONTEND = $false
$ONLY_BACKEND = $false
$START_MCP_MOCK = $false
$CONTAINER_CMD = $null
$COMPOSE_CMD = $null

# =============================================================================
# ARGUMENT PARSING
# =============================================================================
# Hand-parse arguments so both the short (-f) and GNU-style long (--frontend-only)
# spellings work, including --env-file=<path>. PowerShell's param() binder only
# recognizes single-dash parameter names, so --help/--env-file would otherwise
# abort with a binding error before this script runs. This mirrors the
# parse_arguments case statement in agent_start.sh.
$HelpRequested = $false
$EnvFileArg = $null

$i = 0
while ($i -lt $args.Count) {
    $a = [string]$args[$i]
    if ($a -in @("-f", "--frontend-only")) {
        $ONLY_FRONTEND = $true; $i += 1
    } elseif ($a -in @("-b", "--backend-only")) {
        $ONLY_BACKEND = $true; $i += 1
    } elseif ($a -in @("-m", "--mcp-mock")) {
        $START_MCP_MOCK = $true; $i += 1
    } elseif ($a -in @("-h", "--help")) {
        $HelpRequested = $true; $i += 1
    } elseif ($a -in @("-e", "--env-file")) {
        if ($i + 1 -ge $args.Count) {
            Write-Host "Error: --env-file requires an argument"
            exit 1
        }
        $EnvFileArg = [string]$args[$i + 1]; $i += 2
    } elseif ($a -match '^--env-file=(.*)$') {
        $EnvFileArg = $Matches[1]; $i += 1
    } elseif ($a -match '^-e=(.*)$') {
        $EnvFileArg = $Matches[1]; $i += 1
    } else {
        Write-Host "Unknown argument: $a"
        Write-Host "Run with --help for usage."
        exit 1
    }
}

# Path to the .env file to load. Defaults to ATLAS_ENV_FILE env var if set,
# otherwise <project_root>/.env. Can be overridden with -e/--env-file.
# ENV_FILE_EXPLICIT tracks whether the user pointed us at a specific file so
# that a missing explicit file fails loudly (matching agent_start.sh).
if ($EnvFileArg) {
    $script:ENV_FILE = $EnvFileArg
    $script:ENV_FILE_EXPLICIT = $true
} elseif ($env:ATLAS_ENV_FILE) {
    $script:ENV_FILE = $env:ATLAS_ENV_FILE
    $script:ENV_FILE_EXPLICIT = $true
} else {
    $script:ENV_FILE = "$PROJECT_ROOT/.env"
    $script:ENV_FILE_EXPLICIT = $false
}

# Expand ~ in env file path if user provided one (matches bash's tilde expansion)
if ($script:ENV_FILE.StartsWith("~")) {
    $userProfile = [System.Environment]::GetFolderPath("UserProfile")
    $script:ENV_FILE = $userProfile + $script:ENV_FILE.Substring(1)
}

# =============================================================================
# HELP
# =============================================================================

function Show-Help {
    $scriptName = Split-Path -Leaf $SCRIPT_PATH
    Write-Host "Usage: .\$scriptName [options]"
    Write-Host "  -f, --frontend-only        Only rebuild frontend"
    Write-Host "  -b, --backend-only         Only start backend"
    Write-Host "  -m, --mcp-mock             Start MCP mock server"
    Write-Host "  -e, --env-file <path>      Path to .env file to load"
    Write-Host "                              (default: `$ATLAS_ENV_FILE or <project>\.env)"
    Write-Host "  -h, --help                 Show this help message"
    Write-Host ""
    Write-Host "The env file location can also be set via the ATLAS_ENV_FILE environment"
    Write-Host "variable, which is useful for shared installs where each user keeps API"
    Write-Host "keys in a personal file such as ~/.atlasrc."
}

if ($HelpRequested) {
    Show-Help
    exit 0
}

# =============================================================================
# CLEANUP FUNCTIONS
# =============================================================================

function Stop-Mcp {
    if ($null -ne $MCP_PID -and !$MCP_PID.HasExited) {
        Write-Host "Stopping MCP mock server (PID: $($MCP_PID.Id))..."
        $MCP_PID.Kill()
        $MCP_PID.WaitForExit()
        Write-Host "MCP mock server stopped."
    }
}

function Stop-Uvicorn {
    if ($null -ne $UVICORN_PID -and !$UVICORN_PID.HasExited) {
        Write-Host "Stopping uvicorn server (PID: $($UVICORN_PID.Id))..."
        $UVICORN_PID.Kill()
        $UVICORN_PID.WaitForExit()
        Write-Host "Uvicorn server stopped."
    }
}

function Stop-Processes {
    Write-Host "Killing any running uvicorn processes for main backend..."

    # Kill uvicorn processes using the backend main:app pattern
    # Use Get-CimInstance to access CommandLine property
    $uvicornProcesses = Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq "uvicorn.exe" -or $_.Name -eq "python.exe") -and
        $_.CommandLine -like "*uvicorn*main:app*"
    }

    foreach ($proc in $uvicornProcesses) {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped process $($proc.ProcessId)"
        } catch {
            # Process might already be dead, continue silently
        }
    }

    Start-Sleep -Seconds 2
    Clear-Host
}

function Clear-Logs {
    Write-Host "Clearing log for fresh start"
    New-Item -ItemType Directory -Path "$PROJECT_ROOT/logs" -Force | Out-Null
    [System.IO.File]::WriteAllText("$PROJECT_ROOT/logs/app.jsonl", "NEW LOG`n", [System.Text.Encoding]::UTF8)
}

# =============================================================================
# CONTAINER RUNTIME DETECTION
# =============================================================================

function Initialize-ContainerRuntime {
    # Detect if podman or docker is available
    $script:CONTAINER_CMD = $null
    $script:COMPOSE_CMD = $null

    # Check for podman first
    try {
        $null = Get-Command podman -ErrorAction Stop
        $script:CONTAINER_CMD = "podman"

        # Check for podman-compose or podman compose
        try {
            $null = Get-Command podman-compose -ErrorAction Stop
            $script:COMPOSE_CMD = "podman-compose"
        } catch {
            # Use podman compose (newer versions)
            $script:COMPOSE_CMD = "podman compose"
        }

        Write-Host "Using Podman as container runtime"
    } catch {
        # Podman not found, try docker
        try {
            $null = Get-Command docker -ErrorAction Stop
            $script:CONTAINER_CMD = "docker"
            $script:COMPOSE_CMD = "docker-compose"

            # Check if docker compose (v2) is available. A native command does
            # not throw on a non-zero exit (and -ErrorAction is passed through
            # to docker as an argument), so judge by $LASTEXITCODE rather than
            # a try/catch -- otherwise the docker-compose v1 fallback is
            # unreachable.
            & docker compose version 2>$null 1>$null
            if ($LASTEXITCODE -eq 0) {
                $script:COMPOSE_CMD = "docker compose"
            }
            # else: leave it as the docker-compose v1 default set above

            Write-Host "Using Docker as container runtime"
        } catch {
            # Docker not found
            Write-Warning "Neither Docker nor Podman found. Container operations will be skipped."
        }
    }

    # Pre-split the (possibly space-separated) compose command into a head
    # scalar and a tail string array so callers can invoke it uniformly with
    # `& $script:COMPOSE_HEAD @script:COMPOSE_TAIL <args>`. Doing this once
    # here avoids the multi-assignment unpacking ambiguity that bites when a
    # function returns a variable number of elements.
    if ($script:COMPOSE_CMD) {
        $parts = $script:COMPOSE_CMD -split " "
        $script:COMPOSE_HEAD = $parts[0]
        if ($parts.Length -gt 1) {
            $script:COMPOSE_TAIL = [string[]]@($parts[1..($parts.Length - 1)])
        } else {
            $script:COMPOSE_TAIL = [string[]]@()
        }
    } else {
        $script:COMPOSE_HEAD = $null
        $script:COMPOSE_TAIL = $null
    }
}

# =============================================================================
# INFRASTRUCTURE FUNCTIONS
# =============================================================================

function Initialize-MinIO {
    # Initialize-Environment runs first and loads the env file (quote-aware) into
    # the process environment, so read the already-parsed value here rather than
    # re-scraping the file (a raw regex match would keep surrounding quotes and
    # misclassify USE_MOCK_S3="false").
    $useMockS3 = $env:USE_MOCK_S3
    if (-not $useMockS3) {
        $useMockS3 = "true"
    }

    if ($useMockS3 -eq "true") {
        Write-Host "Using Mock S3 (no Docker/Podman required)"
    } else {
        if ($null -eq $script:CONTAINER_CMD) {
            Write-Error "Container runtime not available. Please install Docker or Podman, or set USE_MOCK_S3=true in $script:ENV_FILE"
            exit 1
        }

        # Check if MinIO container is running
        $minioRunning = & $script:CONTAINER_CMD ps 2>$null | Select-String -Pattern "atlas-minio" -Quiet

        if (-not $minioRunning) {
            Write-Host "MinIO is not running. Starting MinIO with $script:COMPOSE_CMD..."
            Set-Location $PROJECT_ROOT

            & $script:COMPOSE_HEAD @script:COMPOSE_TAIL up -d minio minio-init

            Write-Host "MinIO started successfully"
            Start-Sleep -Seconds 3
        } else {
            Write-Host "MinIO is already running"
        }
    }
    Set-Location $PROJECT_ROOT
}

function Initialize-ChatHistoryDb {
    # Initialize-Environment runs first and loads the env file (quote-aware) into
    # the process environment, so read the already-parsed values here. A raw
    # file re-scrape would keep surrounding quotes, so
    # FEATURE_CHAT_HISTORY_ENABLED="true" would fail the -ne "true" test and
    # silently skip chat-history setup.
    $chatHistoryEnabled = $env:FEATURE_CHAT_HISTORY_ENABLED
    if (-not $chatHistoryEnabled) { $chatHistoryEnabled = "false" }
    $dbUrl = $env:CHAT_HISTORY_DB_URL
    if (-not $dbUrl) { $dbUrl = "" }

    if ($chatHistoryEnabled -ne "true") {
        Write-Host "Chat history disabled (FEATURE_CHAT_HISTORY_ENABLED != true)"
        return
    }

    # Default to DuckDB if no URL specified
    if (-not $dbUrl) {
        $dbUrl = "duckdb:///data/chat_history.db"
    }

    if ($dbUrl -match "^postgresql") {
        Write-Host "Chat history: PostgreSQL mode"
        if ($null -eq $script:CONTAINER_CMD) {
            Write-Error "PostgreSQL requires Docker/Podman. Install one or switch to DuckDB."
            Write-Host "  DuckDB: CHAT_HISTORY_DB_URL=duckdb:///data/chat_history.db"
            exit 1
        }

        $pgRunning = & $script:CONTAINER_CMD ps 2>$null | Select-String -Pattern "atlas-postgres" -Quiet
        if (-not $pgRunning) {
            Write-Host "PostgreSQL is not running. Starting with $script:COMPOSE_CMD..."
            Set-Location $PROJECT_ROOT

            & $script:COMPOSE_HEAD @script:COMPOSE_TAIL up -d postgres

            Write-Host "Waiting for PostgreSQL to be ready..."
            $maxWait = 60
            $waited = 0
            $ready = $false
            while ($waited -lt $maxWait) {
                & $script:COMPOSE_HEAD @script:COMPOSE_TAIL exec -T postgres pg_isready 2>$null 1>$null
                if ($LASTEXITCODE -eq 0) {
                    $ready = $true
                    break
                }
                Start-Sleep -Seconds 2
                $waited += 2
            }
            if (-not $ready) {
                Write-Error "PostgreSQL did not become ready within ${maxWait}s."
                exit 1
            }
            Write-Host "PostgreSQL is ready."
        } else {
            Write-Host "PostgreSQL is already running"
        }
    } elseif ($dbUrl -match "^duckdb") {
        Write-Host "Chat history: DuckDB mode"
        # Ensure data directory exists for the DuckDB file
        New-Item -ItemType Directory -Path "$PROJECT_ROOT/data" -Force | Out-Null
    } else {
        Write-Host "Chat history: custom DB URL configured"
    }
    Set-Location $PROJECT_ROOT
}

function Initialize-Environment {
    Set-Location $PROJECT_ROOT

    # Check if .venv exists
    if (-not (Test-Path "$PROJECT_ROOT/.venv")) {
        Write-Error "Virtual environment not found at $PROJECT_ROOT/.venv"
        Write-Host "Please run: uv venv && uv pip install -e '.[dev]'"
        exit 1
    }

    # Check if uvicorn is installed (Scripts directory on Windows)
    $uvicornPath = "$PROJECT_ROOT/.venv/Scripts/uvicorn.exe"
    if (-not (Test-Path $uvicornPath)) {
        Write-Error "uvicorn not found in virtual environment"
        Write-Host "Please run: uv pip install -e '.[dev]'"
        exit 1
    }

    # Activate virtual environment (PowerShell equivalent)
    & "$PROJECT_ROOT/.venv/Scripts/Activate.ps1"

    # Load environment variables from the env file if present
    if (Test-Path $script:ENV_FILE) {
        Write-Host "Loading environment variables from: $script:ENV_FILE"
        Import-DotEnv -Path $script:ENV_FILE
        # Make the resolved env file path available to child processes that
        # consult ATLAS_ENV_FILE so they load the same file. Only set when the
        # file actually exists; pointing children at a missing path would make
        # env-var-aware commands (e.g. atlas-chat) fail to start.
        $env:ATLAS_ENV_FILE = $script:ENV_FILE
    } elseif ($script:ENV_FILE_EXPLICIT) {
        # User explicitly pointed us at a file (via -e/--env-file or ATLAS_ENV_FILE)
        # that does not exist. Fail loudly so missing API keys are obvious.
        Write-Error "Error: env file not found: $script:ENV_FILE"
        exit 1
    }

    Write-Host "Setting MCP_EXTERNAL_API_TOKEN for testing purposes."
    if (-not $env:MCP_EXTERNAL_API_TOKEN) {
        $env:MCP_EXTERNAL_API_TOKEN = "test-api-key-123"
    }
    Set-Location $PROJECT_ROOT
}

# Parse a .env file the way bash's `set -a; . "$ENV_FILE"; set +a` does:
# skip blank/comment lines, strip an optional `export ` prefix, split on the
# first `=`, and resolve the value with Resolve-DotEnvValue. Variables are set
# at Process scope so child processes (uvicorn, npm) inherit them.
#
# Like pydantic-settings' own dotenv loader (and unlike a real shell source),
# this does NOT expand $VAR references -- `API_URL="${BASE}/api"` loads
# literally. That is intentional: the app's pydantic-settings parser also reads
# the same file without expansion, so matching it here keeps the two paths
# consistent. Shell-style ${VAR} expansion would silently diverge.
function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return }
    foreach ($rawLine in Get-Content $Path) {
        $line = $rawLine.Trim()
        if ($line -eq "" -or $line.StartsWith("#")) { continue }
        # Strip an optional "export " prefix
        if ($line -match "^export\s+") {
            $line = $line -replace "^export\s+", ""
        }
        $idx = $line.IndexOf("=")
        if ($idx -lt 0) { continue }
        $key = $line.Substring(0, $idx).Trim()
        if ($key -eq "") { continue }
        $value = Resolve-DotEnvValue -Raw $line.Substring($idx + 1)
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

# Resolve a raw .env value by scanning it the way bash parses a word after `=`:
# quoted segments ("..." / '...') are taken literally and concatenated with
# adjacent unquoted text, and a `#` starts a comment only when it begins a word
# (i.e. is preceded by whitespace or is the first non-whitespace character).
# This handles the common cases faithfully, including:
#   KEY= # set later           -> ""   (whole value is a comment)
#   KEY="bar"#baz              -> bar#baz  (# abuts the closing quote, no comment)
#   KEY="bar" # comment        -> bar  (whitespace before # starts a comment)
#   KEY="a=b=c"                -> a=b=c
# Escaped quotes inside double quotes (\"...) are not supported -- they are not
# used in any in-repo .env file and pydantic-settings handles them differently.
function Resolve-DotEnvValue {
    param([string]$Raw)
    $s = $Raw
    $n = $s.Length
    $result = [System.Text.StringBuilder]::new()
    # Track whether each appended character was inside quotes, so trailing
    # whitespace can be trimmed only when it was unquoted -- `KEY="value "`
    # must keep its protected trailing space, matching bash.
    $quoted = [System.Collections.Generic.List[bool]]::new()
    $i = 0
    # Skip leading whitespace
    while ($i -lt $n -and ($s[$i] -eq ' ' -or $s[$i] -eq "`t")) { $i++ }
    # A leading '#' (possibly after the whitespace skipped above) means the
    # entire value is a comment -> empty.
    if ($i -lt $n -and $s[$i] -eq '#') { return "" }
    while ($i -lt $n) {
        $ch = $s[$i]
        if ($ch -eq '"') {
            $i++
            while ($i -lt $n -and $s[$i] -ne '"') { $null = $result.Append($s[$i]); $quoted.Add($true); $i++ }
            if ($i -lt $n) { $i++ }  # skip closing quote
        } elseif ($ch -eq "'") {
            $i++
            while ($i -lt $n -and $s[$i] -ne "'") { $null = $result.Append($s[$i]); $quoted.Add($true); $i++ }
            if ($i -lt $n) { $i++ }  # skip closing quote
        } elseif ($ch -eq '#' -and $i -gt 0 -and ($s[$i - 1] -eq ' ' -or $s[$i - 1] -eq "`t")) {
            # Whitespace-introduced comment -> stop.
            break
        } else {
            $null = $result.Append($ch)
            $quoted.Add($false)
            $i++
        }
    }
    $str = $result.ToString()
    # Trim trailing whitespace that was NOT inside quotes.
    $end = $str.Length
    while ($end -gt 0 -and ($str[$end - 1] -eq ' ' -or $str[$end - 1] -eq "`t") -and -not $quoted[$end - 1]) {
        $end--
    }
    return $str.Substring(0, $end)
}

# =============================================================================
# MCP MOCK SERVER FUNCTIONS
# =============================================================================

function Start-McpMock {
    if ($START_MCP_MOCK) {
        Write-Host "Starting MCP mock server..."
        Set-Location "$PROJECT_ROOT/mocks/mcp-http-mock"
        # Set the default tokens in the process environment (mirrors run.bat /
        # run.sh) so the server sees them, then launch python directly. Going
        # through `cmd /c run.bat` would return the cmd.exe wrapper PID, and on
        # Windows killing that wrapper does not terminate the child python
        # process -- the mock would keep listening on its port after cleanup.
        # Starting python directly gives Stop-Mcp the real server process.
        if (-not $env:MCP_MOCK_TOKEN_1) { $env:MCP_MOCK_TOKEN_1 = "test-api-key-123" }
        if (-not $env:MCP_MOCK_TOKEN_2) { $env:MCP_MOCK_TOKEN_2 = "another-test-key-456" }
        $script:MCP_PID = Start-Process -FilePath "python" -ArgumentList "main.py" -PassThru -NoNewWindow -WorkingDirectory "$PROJECT_ROOT/mocks/mcp-http-mock"
        Write-Host "MCP mock server started with PID: $($MCP_PID.Id)"
        Set-Location $PROJECT_ROOT
    }
}

# =============================================================================
# FRONTEND BUILD FUNCTIONS
# =============================================================================

function Build-Frontend {
    Write-Host "Building frontend..."
    Set-Location "$PROJECT_ROOT/frontend"
    npm install
    if ($LASTEXITCODE -ne 0) {
        Set-Location $PROJECT_ROOT
        throw "npm install failed (exit code $LASTEXITCODE)."
    }

    # Use VITE_* values from the environment / .env instead of hardcoding.
    # If VITE_APP_NAME is not already set, fall back to the example default.
    if (-not $env:VITE_APP_NAME) {
        $env:VITE_APP_NAME = "ATLAS"
    }

    # RAG citations UI is opt-in at build time; default to off if unset.
    if (-not $env:VITE_FEATURE_RAG_CITATIONS) {
        $env:VITE_FEATURE_RAG_CITATIONS = "false"
    }

    npm run build
    $buildStatus = $LASTEXITCODE
    Set-Location $PROJECT_ROOT

    if ($buildStatus -ne 0) {
        Write-Host "ERROR: Frontend build failed (npm run build exit code $buildStatus)."
        Write-Host "Keeping existing atlas/static/ assets; aborting build copy."
        throw "Frontend build failed (exit code $buildStatus)."
    }

    # Copy build output to atlas/static/ so the backend always serves from one
    # location, matching the PyPI package layout.
    if (-not (Test-Path "$PROJECT_ROOT/frontend/dist")) {
        Write-Host "ERROR: frontend/dist not found after build; keeping existing atlas/static/ assets."
        throw "frontend/dist not found after build."
    }
    if (Test-Path "$PROJECT_ROOT/atlas/static") {
        Remove-Item -Recurse -Force "$PROJECT_ROOT/atlas/static"
    }
    Copy-Item -Recurse "$PROJECT_ROOT/frontend/dist" "$PROJECT_ROOT/atlas/static"
}

# =============================================================================
# BACKEND SERVER FUNCTIONS
# =============================================================================

function Start-Backend {
    param(
        [int]$Port = 8000,
        [string]$HostName = "127.0.0.1"
    )

    Set-Location "$PROJECT_ROOT/atlas"
    # The atlas package is installed in editable mode (pip install -e .), so
    # PYTHONPATH is no longer needed for atlas imports to work.
    # Set APP_CONFIG_DIR so user overrides in <project_root>/config/ take
    # precedence over package defaults in atlas/config/ (CWD is atlas/).
    if (-not $env:APP_CONFIG_DIR) {
        $env:APP_CONFIG_DIR = "$PROJECT_ROOT/config"
    } elseif (-not (Test-Path -LiteralPath $env:APP_CONFIG_DIR -PathType Container)) {
        # A stale APP_CONFIG_DIR (commonly a POSIX-style path such as
        # /home/<user>/... left in .env from a WSL/Linux setup) is drive-less
        # under Windows path semantics, so the backend would search a bogus
        # "C:\home\..." location, find no mcp.json, and start with 0 MCP
        # servers even though <project_root>\config\mcp.json exists. Fall
        # back to the project config dir and say why.
        Write-Warning "APP_CONFIG_DIR points at '$($env:APP_CONFIG_DIR)', which does not exist on this machine. Falling back to '$PROJECT_ROOT/config' (a stale POSIX-style path from WSL/Linux in .env is a common cause)."
        $env:APP_CONFIG_DIR = "$PROJECT_ROOT/config"
    }
    $uvicornExe = "$PROJECT_ROOT/.venv/Scripts/uvicorn.exe"
    $arguments = "main:app --host $HostName --port $Port"

    $script:UVICORN_PID = Start-Process -FilePath $uvicornExe -ArgumentList $arguments -PassThru -NoNewWindow

    Write-Host "Backend server started on ${HostName}:$Port (PID: $($script:UVICORN_PID.Id))"
    Set-Location $PROJECT_ROOT
}

# =============================================================================
# MAIN EXECUTION FLOW
# =============================================================================

function Main {
    # Setup infrastructure
    # Note: Initialize-Environment must run first to activate the venv so the
    # venv's podman-compose (if installed there) is found, matching agent_start.sh.
    Initialize-Environment
    Initialize-ContainerRuntime
    Initialize-MinIO
    Initialize-ChatHistoryDb

    # Handle frontend-only mode
    if ($ONLY_FRONTEND) {
        try {
            Build-Frontend
        } catch {
            Write-Host "ERROR: Frontend build failed. Aborting."
            exit 1
        }
        Write-Host "Frontend rebuilt successfully. Exiting as requested."
        exit 0
    }

    # Handle backend-only mode
    if ($ONLY_BACKEND) {
        Stop-Processes
        Clear-Logs
        Start-McpMock
        $backendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }
        $backendHost = if ($env:ATLAS_HOST) { $env:ATLAS_HOST } else { "127.0.0.1" }
        Start-Backend -Port $backendPort -HostName $backendHost
        Write-Host "Backend server started."
        Write-Host "Press Ctrl+C to stop all services."

        # Keep script running to prevent cleanup
        try {
            while ($true) {
                Start-Sleep -Seconds 1
            }
        }
        finally {
            Stop-Mcp
            Stop-Uvicorn
        }
        exit 0
    }

    # Full startup mode (default)
    Stop-Processes
    Clear-Logs
    try {
        Build-Frontend
    } catch {
        Write-Host "ERROR: Frontend build failed. Aborting startup."
        exit 1
    }
    Start-McpMock
    $backendPort = if ($env:PORT) { [int]$env:PORT } else { 8000 }
    $backendHost = if ($env:ATLAS_HOST) { $env:ATLAS_HOST } else { "127.0.0.1" }
    Start-Backend -Port $backendPort -HostName $backendHost

    # Display MCP info if started
    if ($START_MCP_MOCK) {
        Write-Host "MCP mock server is running with PID: $($MCP_PID.Id)"
        Write-Host "To stop the MCP mock server manually, run: taskkill /PID $($MCP_PID.Id)"
    }

    Write-Host "All services started. Press Ctrl+C to stop."
    Set-Location $PROJECT_ROOT

    # Keep script running to prevent cleanup
    try {
        while ($true) {
            Start-Sleep -Seconds 1
        }
    }
    finally {
        Stop-Mcp
        Stop-Uvicorn
    }
}

# Cleanup is handled by the finally blocks in the Main function and surrounding try blocks

# Run main function
try {
    Main
}
finally {
    Stop-Mcp
    Stop-Uvicorn
}

# # PowerShell equivalents for the commented-out bash code:
# #
# # Print every 3 seconds saying it is running. Do 10 times. Print seconds since start
# # for ($i = 1; $i -le 10; $i++) {
# #     Write-Host "Server running for $(3 * $i) seconds"
# #     Start-Sleep -Seconds 3
# # }
# #
# # Wait X seconds.
# # $waittime = 10
# # Write-Host "Starting server, waiting for $waittime seconds before sending config request"
# # for ($i = $waittime; $i -gt 0; $i--) {
# #     Write-Host "Waiting... $i seconds remaining"
# #     Start-Sleep -Seconds 1
# # }
# #
# # Send HTTP request to config endpoint (requires Invoke-WebRequest or curl)
# # $host = "127.0.0.1"
# # Write-Host "Sending config request to ${host}:8000/api/config"
# # try {
# #     $response = Invoke-WebRequest -Uri "http://${host}:8000/api/config" -Method GET -ContentType "application/json"
# #     $result = $response.Content | ConvertFrom-Json
# #     Write-Host "Config request result:"
# #     $result.tools | ConvertTo-Json
# # } catch {
# #     Write-Host "Error making config request: $_"
# # }
# #
# # Make a count for 20 seconds and prompt the human to cause any errors
# # Write-Host "Server ready, you can now cause any errors in the UI"
# # for ($i = 20; $i -gt 0; $i--) {
# #     Write-Host "You have $i seconds to cause any errors in the UI"
# #     Start-Sleep -Seconds 1
# # }