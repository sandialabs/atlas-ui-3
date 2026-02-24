"""Tests for the tool_planner MCP server."""

import base64
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Load tool_planner/main.py as a uniquely-named module to avoid colliding
# with backend/main.py which is already on sys.path.
_tool_planner_path = Path(__file__).parent.parent / "mcp" / "tool_planner" / "main.py"
_spec = importlib.util.spec_from_file_location("tool_planner_main", _tool_planner_path)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

format_tools_for_llm = _mod.format_tools_for_llm
build_planning_prompt = _mod.build_planning_prompt
_build_artifact_response = _mod._build_artifact_response
# plan_with_tools may be wrapped by @mcp.tool into a FunctionTool (.fn attr)
# or left as a plain function depending on the fastmcp version
_plan_with_tools_fn = getattr(_mod.plan_with_tools, 'fn', _mod.plan_with_tools)


def _decode_artifact(result: dict) -> str:
    """Decode the base64 script content from an artifact response."""
    return base64.b64decode(result["artifacts"][0]["b64"]).decode("utf-8")


async def _call_plan_with_tools(**kwargs):
    """Call the unwrapped plan_with_tools async function."""
    return await _plan_with_tools_fn(**kwargs)


# ---------------------------------------------------------------------------
# format_tools_for_llm tests
# ---------------------------------------------------------------------------

class TestFormatToolsForLlm:
    """Tests for the format_tools_for_llm helper."""

    def test_empty_data_returns_no_tools_message(self):
        result = format_tools_for_llm({})
        assert result == "(No tools available)"

    def test_empty_servers_returns_no_tools_message(self):
        result = format_tools_for_llm({"available_servers": []})
        assert result == "(No tools available)"

    def test_single_server_single_tool(self):
        data = {
            "available_servers": [
                {
                    "server_name": "calculator",
                    "description": "Math calculator",
                    "tools": [
                        {
                            "name": "calculator_evaluate",
                            "description": "Evaluate math expressions",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "expression": {
                                        "type": "string",
                                        "description": "Math expression",
                                    }
                                },
                                "required": ["expression"],
                            },
                        }
                    ],
                }
            ]
        }
        result = format_tools_for_llm(data)
        assert "Server: calculator (Math calculator)" in result
        assert "Tool: calculator_evaluate" in result
        assert "Description: Evaluate math expressions" in result
        assert "expression (string, required): Math expression" in result

    def test_multiple_servers_and_tools(self):
        data = {
            "available_servers": [
                {
                    "server_name": "calc",
                    "description": "",
                    "tools": [
                        {"name": "calc_add", "description": "Add numbers", "parameters": {}},
                    ],
                },
                {
                    "server_name": "pptx",
                    "description": "Slides",
                    "tools": [
                        {"name": "pptx_create", "description": "Create slides", "parameters": {}},
                        {"name": "pptx_export", "description": "Export slides", "parameters": {}},
                    ],
                },
            ]
        }
        result = format_tools_for_llm(data)
        assert "Server: calc" in result
        assert "Server: pptx (Slides)" in result
        assert "Tool: calc_add" in result
        assert "Tool: pptx_create" in result
        assert "Tool: pptx_export" in result

    def test_optional_and_required_params(self):
        data = {
            "available_servers": [
                {
                    "server_name": "srv",
                    "description": "",
                    "tools": [
                        {
                            "name": "srv_tool",
                            "description": "",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "required_param": {"type": "string"},
                                    "optional_param": {"type": "number"},
                                },
                                "required": ["required_param"],
                            },
                        }
                    ],
                }
            ]
        }
        result = format_tools_for_llm(data)
        assert "required_param (string, required)" in result
        assert "optional_param (number, optional)" in result

    def test_underscore_prefixed_params_excluded(self):
        data = {
            "available_servers": [
                {
                    "server_name": "srv",
                    "description": "",
                    "tools": [
                        {
                            "name": "srv_tool",
                            "description": "",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "task": {"type": "string"},
                                    "_mcp_data": {"type": "object"},
                                },
                            },
                        }
                    ],
                }
            ]
        }
        result = format_tools_for_llm(data)
        assert "_mcp_data" not in result
        assert "task (string" in result


# ---------------------------------------------------------------------------
# build_planning_prompt tests
# ---------------------------------------------------------------------------

class TestBuildPlanningPrompt:
    """Tests for the build_planning_prompt helper."""

    def test_includes_task(self):
        result = build_planning_prompt("create a presentation", "some tools")
        assert "Task: create a presentation" in result

    def test_includes_tools_reference(self):
        result = build_planning_prompt("task", "Server: calc\n  Tool: calc_add")
        assert "Server: calc" in result
        assert "Tool: calc_add" in result

    def test_includes_cli_usage_instructions(self):
        result = build_planning_prompt("task", "tools")
        assert "python atlas_chat_cli.py" in result
        assert "--tools tool_name" in result
        assert "-o result.txt" in result

    def test_includes_loop_example(self):
        result = build_planning_prompt("task", "tools")
        assert "for item" in result


# ---------------------------------------------------------------------------
# plan_with_tools tests
# ---------------------------------------------------------------------------

class TestBuildArtifactResponse:
    """Tests for the _build_artifact_response helper."""

    def test_returns_required_keys(self):
        result = _build_artifact_response("#!/bin/bash\necho hi", "greet user")
        assert "results" in result
        assert "artifacts" in result
        assert "display" in result

    def test_artifact_contains_base64_script(self):
        script = "#!/bin/bash\nset -e\necho hello"
        result = _build_artifact_response(script, "say hello")
        artifact = result["artifacts"][0]
        decoded = base64.b64decode(artifact["b64"]).decode("utf-8")
        assert decoded == script

    def test_artifact_mime_and_viewer(self):
        result = _build_artifact_response("echo x", "task")
        artifact = result["artifacts"][0]
        assert artifact["mime"] == "application/x-sh"
        assert artifact["viewer"] == "code"

    def test_filename_derived_from_task(self):
        result = _build_artifact_response("echo x", "Create a PowerPoint about dogs")
        filename = result["artifacts"][0]["name"]
        assert filename.endswith(".sh")
        assert "create" in filename
        assert " " not in filename

    def test_display_opens_canvas_with_code_hint(self):
        result = _build_artifact_response("echo x", "task")
        display = result["display"]
        assert display["open_canvas"] is True
        assert display["viewer_hint"] == "code"
        assert display["primary_file"].endswith(".sh")


class TestPlanWithTools:
    """Tests for the plan_with_tools tool function."""

    @pytest.mark.asyncio
    async def test_without_ctx_returns_artifact(self):
        result = await _call_plan_with_tools(
            task="test task", _mcp_data={"available_servers": []}
        )
        assert result["results"]["operation"] == "plan_with_tools"
        script = _decode_artifact(result)
        assert "Sampling unavailable" in script
        assert "test task" in script

    @pytest.mark.asyncio
    async def test_without_mcp_data_still_works(self):
        result = await _call_plan_with_tools(task="do something")
        script = _decode_artifact(result)
        assert "Sampling unavailable" in script
        assert "No tools available" in script

    @pytest.mark.asyncio
    async def test_with_mocked_ctx_sample(self):
        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.text = "#!/bin/bash\nset -e\npython atlas_chat_cli.py 'hello' --tools calc_add"
        mock_ctx.sample = AsyncMock(return_value=mock_result)
        mock_ctx.report_progress = AsyncMock()

        mcp_data = {
            "available_servers": [
                {
                    "server_name": "calc",
                    "description": "Calculator",
                    "tools": [
                        {
                            "name": "calc_add",
                            "description": "Add numbers",
                            "parameters": {
                                "type": "object",
                                "properties": {"a": {"type": "number"}},
                            },
                        }
                    ],
                }
            ]
        }

        result = await _call_plan_with_tools(
            task="add two numbers", _mcp_data=mcp_data, ctx=mock_ctx
        )

        script = _decode_artifact(result)
        assert "atlas_chat_cli.py" in script
        assert result["artifacts"][0]["name"].endswith(".sh")
        mock_ctx.sample.assert_awaited_once()

        call_kwargs = mock_ctx.sample.call_args
        assert call_kwargs.kwargs["temperature"] == 0.3
        assert call_kwargs.kwargs["max_tokens"] == 10000
        assert "task planner" in call_kwargs.kwargs["system_prompt"].lower()

    @pytest.mark.asyncio
    async def test_sample_returns_none_text(self):
        mock_ctx = MagicMock()
        mock_result = MagicMock()
        mock_result.text = None
        mock_ctx.sample = AsyncMock(return_value=mock_result)
        mock_ctx.report_progress = AsyncMock()

        result = await _call_plan_with_tools(task="test", _mcp_data={}, ctx=mock_ctx)
        script = _decode_artifact(result)
        assert "Unable to generate plan" in script
