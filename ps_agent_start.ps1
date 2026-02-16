#!/usr/bin/env powershell

<#
.SYNOPSIS
    PowerShell equivalent of agent_start.sh for Windows bare metal environment

.DESCRIPTION
    This script starts the application services (backend, frontend, and optionally MCP mock)
    in a Windows environment with PowerShell.

.PARAMETER FrontendOnly
    Only rebuild frontend

.PARAMETER BackendOnly
    Only start backend

.PARAMETER StartMcpMock
    Start MCP mock server
#>

param(
    [switch]$FrontendOnly,
    [switch]$BackendOnly,
    [switch]$StartMcpMock
)

# Store the project root directory
$PROJECT_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $PROJECT_ROOT

# Global variables
$MCP_PID = $null
$UVICORN_PID = $null
$ONLY_FRONTEND = $FrontendOnly
$ONLY_BACKEND = $BackendOnly
$START_MCP_MOCK = $StartMcpMock
$CONTAINER_CMD = $null
$COMPOSE_CMD = $null

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
    "NEW LOG" | Out-File -FilePath "$PROJECT_ROOT/logs/app.jsonl"
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
        return
    } catch {
        # Podman not found, try docker
    }

    # Check for docker
    try {
        $null = Get-Command docker -ErrorAction Stop
        $script:CONTAINER_CMD = "docker"
        $script:COMPOSE_CMD = "docker-compose"

        # Check if docker compose (v2) is available
        try {
            $null = & docker compose version -ErrorAction SilentlyContinue
            $script:COMPOSE_CMD = "docker compose"
        } catch {
            # Fall back to docker-compose v1
        }

        Write-Host "Using Docker as container runtime"
        return
    } catch {
        # Docker not found
    }

    # Neither found
    Write-Warning "Neither Docker nor Podman found. Container operations will be skipped."
}

# =============================================================================
# INFRASTRUCTURE FUNCTIONS
# =============================================================================

function Initialize-MinIO {
    $useMockS3 = $env:USE_MOCK_S3
    if (-not $useMockS3) {
        $useMockS3 = "true"
    }

    # Read USE_MOCK_S3 from .env file if it exists
    if (Test-Path "$PROJECT_ROOT/.env") {
        $envContent = Get-Content "$PROJECT_ROOT/.env" -Raw
        $match = [regex]::Match($envContent, "USE_MOCK_S3=([^\r\n]+)")
        if ($match.Success) {
            $useMockS3 = $match.Groups[1].Value.Trim()
        }
    }

    if ($useMockS3 -eq "true") {
        Write-Host "Using Mock S3 (no Docker/Podman required)"
    } else {
        if ($null -eq $CONTAINER_CMD) {
            Write-Error "Container runtime not available. Please install Docker or Podman, or set USE_MOCK_S3=true in .env"
            exit 1
        }

        # Check if MinIO container is running
        $minioRunning = & $CONTAINER_CMD ps | Select-String -Pattern "atlas-minio"

        if (-not $minioRunning) {
            Write-Host "MinIO is not running. Starting MinIO with $COMPOSE_CMD..."
            Set-Location $PROJECT_ROOT

            # Handle both space-separated and single command formats
            if ($COMPOSE_CMD -like "* *") {
                $cmdParts = $COMPOSE_CMD -split " "
                & $cmdParts[0] $cmdParts[1..($cmdParts.Length-1)] up -d minio minio-init
            } else {
                & $COMPOSE_CMD up -d minio minio-init
            }

            Write-Host "MinIO started successfully"
            Start-Sleep -Seconds 3
        } else {
            Write-Host "MinIO is already running"
        }
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

    # Check if uvicorn is installed (check Scripts directory on Windows)
    $uvicornPath = "$PROJECT_ROOT/.venv/Scripts/uvicorn.exe"
    if (-not (Test-Path $uvicornPath)) {
        Write-Error "uvicorn not found in virtual environment"
        Write-Host "Please run: uv pip install -e '.[dev]'"
        exit 1
    }

    # Activate virtual environment (PowerShell equivalent)
    & "$PROJECT_ROOT/.venv/Scripts/Activate.ps1"

    # Load environment variables from .env if present
    if (Test-Path "$PROJECT_ROOT/.env") {
        $envContent = Get-Content "$PROJECT_ROOT/.env" -Raw
        $envVars = $envContent -split "`n" | Where-Object { $_ -match "^[^#].*=" }

        foreach ($line in $envVars) {
            $keyValue = $line -split "=", 2
            if ($keyValue.Length -eq 2) {
                $key = $keyValue[0].Trim()
                $value = $keyValue[1].Trim()
                [Environment]::SetEnvironmentVariable($key, $value, "Process")
            }
        }
    }

    Write-Host "Setting MCP_EXTERNAL_API_TOKEN for testing purposes."
    if (-not $env:MCP_EXTERNAL_API_TOKEN) {
        $env:MCP_EXTERNAL_API_TOKEN = "test-api-key-123"
    }
    Set-Location $PROJECT_ROOT
}

# =============================================================================
# MCP MOCK SERVER FUNCTIONS
# =============================================================================

function Start-McpMock {
    if ($START_MCP_MOCK) {
        Write-Host "Starting MCP mock server..."
        Set-Location "$PROJECT_ROOT/mocks/mcp-http-mock"
        $script:MCP_PID = Start-Process -FilePath "cmd.exe" -ArgumentList "/c run.bat" -PassThru -NoNewWindow
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

    # Use VITE_* values from the environment / .env instead of hardcoding.
    # If VITE_APP_NAME is not already set, fall back to the example default.
    if (-not $env:VITE_APP_NAME) {
        $env:VITE_APP_NAME = "Chat UI 13"
    }

    npm run build
    Set-Location $PROJECT_ROOT
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
    Initialize-ContainerRuntime
    Initialize-MinIO
    Initialize-Environment

    # Handle frontend-only mode
    if ($ONLY_FRONTEND) {
        Build-Frontend
        Write-Host "Frontend rebuilt successfully. Exiting as requested."
        exit 0
    }

    # Handle backend-only mode
    if ($ONLY_BACKEND) {
        Stop-Processes
        Clear-Logs
        Start-McpMock
        Start-Backend -Port 8000 -HostName "0.0.0.0"
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
    Build-Frontend
    Start-McpMock
    Start-Backend -Port 8000 -HostName "127.0.0.1"

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
