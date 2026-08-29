@echo off
setlocal
REM MCP HTTP Mock Server Runner (Windows counterpart of run.sh)
echo Starting MCP HTTP Mock Server...

REM Tokens default to well-known test values; existing env vars take precedence.
REM Print only the source (not the value) so a real credential is not echoed
REM into the session transcript. setlocal keeps the defaults scoped to this
REM script so they do not leak into the caller's session.
if not defined MCP_MOCK_TOKEN_1 (
    set MCP_MOCK_TOKEN_1=test-api-key-123
    echo   Token 1: using default
) else (
    echo   Token 1: from environment
)
if not defined MCP_MOCK_TOKEN_2 (
    set MCP_MOCK_TOKEN_2=another-test-key-456
    echo   Token 2: using default
) else (
    echo   Token 2: from environment
)

REM Change to the script directory
cd /d "%~dp0"

REM Run the server
python main.py %*
exit /b %ERRORLEVEL%
