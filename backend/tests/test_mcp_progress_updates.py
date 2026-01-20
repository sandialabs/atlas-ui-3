"""Tests for enhanced MCP progress update notifications."""

import pytest
import json
from unittest.mock import AsyncMock

from application.chat.utilities.event_notifier import (
    notify_tool_progress,
    _handle_structured_progress_update
)


@pytest.mark.asyncio
async def test_notify_tool_progress_regular():
    """Test regular progress notification without structured updates."""
    callback = AsyncMock()
    
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=5,
        total=10,
        message="Processing...",
        update_callback=callback
    )
    
    callback.assert_called_once()
    call_args = callback.call_args[0][0]
    
    assert call_args["type"] == "tool_progress"
    assert call_args["tool_call_id"] == "test-123"
    assert call_args["tool_name"] == "test_tool"
    assert call_args["progress"] == 5
    assert call_args["total"] == 10
    assert call_args["percentage"] == 50.0
    assert call_args["message"] == "Processing..."


@pytest.mark.asyncio
async def test_notify_tool_progress_canvas_update():
    """Test canvas update via structured progress message."""
    callback = AsyncMock()
    
    update_payload = {
        "type": "canvas_update",
        "content": "<html><body>Test</body></html>",
        "progress_message": "Updating canvas"
    }
    
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=5,
        message=f"MCP_UPDATE:{json.dumps(update_payload)}",
        update_callback=callback
    )
    
    # Should be called twice: once for canvas_content, once for tool_progress
    assert callback.call_count == 2
    
    # Check canvas_content message
    canvas_call = callback.call_args_list[0][0][0]
    assert canvas_call["type"] == "canvas_content"
    assert canvas_call["content"] == "<html><body>Test</body></html>"
    
    # Check progress message
    progress_call = callback.call_args_list[1][0][0]
    assert progress_call["type"] == "tool_progress"
    assert progress_call["message"] == "Updating canvas"


@pytest.mark.asyncio
async def test_notify_tool_progress_system_message():
    """Test system message via structured progress message."""
    callback = AsyncMock()
    
    update_payload = {
        "type": "system_message",
        "message": "Stage 1 completed",
        "subtype": "success",
        "progress_message": "Completed stage 1"
    }
    
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=3,
        message=f"MCP_UPDATE:{json.dumps(update_payload)}",
        update_callback=callback
    )
    
    # Should be called twice: once for intermediate_update, once for tool_progress
    assert callback.call_count == 2
    
    # Check system message
    system_call = callback.call_args_list[0][0][0]
    assert system_call["type"] == "intermediate_update"
    assert system_call["update_type"] == "system_message"
    assert system_call["data"]["message"] == "Stage 1 completed"
    assert system_call["data"]["subtype"] == "success"
    assert system_call["data"]["tool_call_id"] == "test-123"
    assert system_call["data"]["tool_name"] == "test_tool"


@pytest.mark.asyncio
async def test_notify_tool_progress_artifacts():
    """Test artifacts via structured progress message."""
    callback = AsyncMock()
    
    update_payload = {
        "type": "artifacts",
        "artifacts": [
            {
                "name": "result.html",
                "b64": "PGh0bWw+PC9odG1sPg==",
                "mime": "text/html",
                "size": 100,
                "viewer": "html"
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "result.html"
        },
        "progress_message": "Generated result"
    }
    
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=2,
        total=3,
        message=f"MCP_UPDATE:{json.dumps(update_payload)}",
        update_callback=callback
    )
    
    # Should be called twice: once for intermediate_update, once for tool_progress
    assert callback.call_count == 2
    
    # Check artifacts message
    artifacts_call = callback.call_args_list[0][0][0]
    assert artifacts_call["type"] == "intermediate_update"
    assert artifacts_call["update_type"] == "progress_artifacts"
    assert len(artifacts_call["data"]["artifacts"]) == 1
    assert artifacts_call["data"]["artifacts"][0]["name"] == "result.html"
    assert artifacts_call["data"]["display"]["open_canvas"] is True


@pytest.mark.asyncio
async def test_notify_tool_progress_artifacts_inline_shape():
    """Progress artifacts should preserve inline-friendly fields for frontend rendering."""
    callback = AsyncMock()

    update_payload = {
        "type": "artifacts",
        "artifacts": [
            {
                "name": "progress_step_1.html",
                "b64": "PGgxPkhlbGxvPC9oMT4=",
                "mime": "text/html",
                "size": 42,
                "description": "Step 1",
                "viewer": "html",
            }
        ],
        "display": {
            "open_canvas": True,
            "primary_file": "progress_step_1.html",
            "mode": "replace",
        },
        "progress_message": "demo: Step 1/3",
    }

    await notify_tool_progress(
        tool_call_id="call-1",
        tool_name="progress_tool",
        progress=1,
        total=3,
        message=f"MCP_UPDATE:{json.dumps(update_payload)}",
        update_callback=callback,
    )

    # First callback should carry the raw artifact fields through untouched
    artifacts_call = callback.call_args_list[0][0][0]
    assert artifacts_call["type"] == "intermediate_update"
    assert artifacts_call["update_type"] == "progress_artifacts"

    data = artifacts_call["data"]
    assert data["tool_call_id"] == "call-1"
    assert data["tool_name"] == "progress_tool"

    assert isinstance(data["artifacts"], list)
    art = data["artifacts"][0]
    # These fields are required for inline rendering on the frontend
    assert art["name"] == "progress_step_1.html"
    assert art["b64"] == "PGgxPkhlbGxvPC9oMT4="
    assert art["mime"] == "text/html"
    assert art["viewer"] == "html"
    assert art["size"] == 42
    assert art["description"] == "Step 1"

    display = data["display"]
    assert display["open_canvas"] is True
    assert display["primary_file"] == "progress_step_1.html"
    assert display["mode"] == "replace"


@pytest.mark.asyncio
async def test_notify_tool_progress_invalid_json():
    """Test that invalid JSON in MCP_UPDATE falls back to regular progress."""
    callback = AsyncMock()
    
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=5,
        message="MCP_UPDATE:{invalid json}",
        update_callback=callback
    )
    
    # Should fall back to regular progress notification
    callback.assert_called_once()
    call_args = callback.call_args[0][0]
    assert call_args["type"] == "tool_progress"
    assert "invalid json" in call_args["message"]


@pytest.mark.asyncio
async def test_notify_tool_progress_no_callback():
    """Test that progress with no callback doesn't raise errors."""
    # Should not raise any exceptions
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=5,
        message="Test",
        update_callback=None
    )


@pytest.mark.asyncio
async def test_handle_structured_progress_update_canvas():
    """Test _handle_structured_progress_update for canvas updates."""
    callback = AsyncMock()
    
    structured_data = {
        "type": "canvas_update",
        "content": "<html>Test</html>",
        "progress_message": "Updating"
    }
    
    await _handle_structured_progress_update(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=5,
        structured_data=structured_data,
        update_callback=callback
    )
    
    # Should send canvas_content and tool_progress
    assert callback.call_count == 2
    assert callback.call_args_list[0][0][0]["type"] == "canvas_content"
    assert callback.call_args_list[1][0][0]["type"] == "tool_progress"


@pytest.mark.asyncio
async def test_percentage_calculation():
    """Test percentage calculation in progress notifications."""
    callback = AsyncMock()
    
    # Test with valid total
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=3,
        total=4,
        message="Test",
        update_callback=callback
    )
    
    call_args = callback.call_args[0][0]
    assert call_args["percentage"] == 75.0
    
    # Test with zero total
    callback.reset_mock()
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=0,
        message="Test",
        update_callback=callback
    )
    
    call_args = callback.call_args[0][0]
    assert call_args["percentage"] is None
    
    # Test with None total (indeterminate progress)
    callback.reset_mock()
    await notify_tool_progress(
        tool_call_id="test-123",
        tool_name="test_tool",
        progress=1,
        total=None,
        message="Test",
        update_callback=callback
    )
    
    call_args = callback.call_args[0][0]
    assert call_args["percentage"] is None
