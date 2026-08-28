@echo off
REM MCP HTTP Mock Server Runner
REM This script sets up environment variables for the mock server
REM and starts it with the appropriate configuration. It is the Windows
REM counterpart of run.sh.

echo Starting MCP HTTP Mock Server...

REM Set default tokens if not already set
if "%MCP_MOCK_TOKEN_1%"=="" set MCP_MOCK_TOKEN_1=test-api-key-123
if "%MCP_MOCK_TOKEN_2%"=="" set MCP_MOCK_TOKEN_2=another-test-key-456

echo Using tokens:
echo   Token 1: %MCP_MOCK_TOKEN_1%
echo   Token 2: %MCP_MOCK_TOKEN_2%

REM Change to the script directory
cd /d "%~dp0"

REM Run the server
python main.py %*