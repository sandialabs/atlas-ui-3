import asyncio
import sys
import os
from pathlib import Path
import pytest

# Add backend to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cli import CLIConnectionAdapter


@pytest.mark.asyncio
async def test_cli_connection_adapter(capsys):
    """Unit test for CLIConnectionAdapter."""
    adapter = CLIConnectionAdapter("test@example.com")

    # Test get_user_email
    assert adapter.get_user_email() == "test@example.com"

    # Test send_json with error message
    await adapter.send_json({"type": "error", "message": "Test error"})
    captured = capsys.readouterr()
    assert "ERROR: Test error" in captured.out

    # Test send_json with normal message
    await adapter.send_json({"type": "token_stream", "content": "Hello"})
    captured = capsys.readouterr()
    assert "Received: token_stream" in captured.out
    assert "Hello" in captured.out
