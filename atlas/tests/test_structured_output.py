"""Tests for structured output parsing priority."""
import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.fixture
def manager():
    """Create a MCPToolManager for testing normalization."""
    return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")


@dataclass
class _RootDataclass:
    """Stand-in for the title-less object dataclass FastMCP 3.x builds
    client-side when validating structuredContent against an object
    output_schema (it is named "Root" when the schema has no title)."""

    query: str
    answers: list


class _RootModel(BaseModel):
    """Pydantic model variant of the same "Root" structured output."""

    query: str
    answers: list


class _RootModelWithArtifacts(BaseModel):
    """Object "Root" result that carries v2 artifacts/display/meta_data."""

    query: str
    answers: list
    artifacts: list
    display: dict
    meta_data: dict


class TestStructuredOutputPriority:
    def test_data_preferred_over_structured_content(self, manager):
        """raw_result.data (validated) takes priority over structured_content (raw)."""
        raw = MagicMock()
        raw.data = {"results": "from-data", "meta_data": {"source": "validated"}}
        raw.structured_content = {"results": "from-structured", "meta_data": {"source": "raw"}}
        raw.content = []

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "from-data"
        assert result["meta_data"]["source"] == "validated"

    def test_structured_content_when_no_data(self, manager):
        """Falls back to structured_content when data is None."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = {"results": "from-structured"}
        raw.content = []

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "from-structured"

    def test_text_fallback_when_no_structured(self, manager):
        """Falls back to content[0].text JSON when neither data nor structured_content."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = None

        text_item = MagicMock()
        text_item.type = "text"
        text_item.text = json.dumps({"results": "from-text"})
        raw.content = [text_item]

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "from-text"

    def test_legacy_keys_still_work(self, manager):
        """Legacy keys (result, meta-data) are still recognized."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = {"result": "legacy-val", "meta-data": {"k": "v"}}
        raw.content = []

        result = manager._normalize_mcp_tool_result(raw)
        assert result["results"] == "legacy-val"
        assert result["meta_data"]["k"] == "v"

    def test_root_dataclass_data_is_json_serializable(self, manager):
        """raw_result.data may be a pydantic/dataclass "Root" instance (FastMCP
        3.x validates object structuredContent client-side). It must be coerced
        to plain JSON-able Python instead of raising
        "Object of type Root is not JSON serializable"."""
        raw = MagicMock()
        raw.data = _RootDataclass(query="hello", answers=["a1", "a2"])
        raw.structured_content = None
        raw.content = []

        normalized = manager._normalize_mcp_tool_result(raw)

        # The dataclass's fields become the structured dict, so the object's
        # own keys are present and the whole thing round-trips through json.
        assert normalized["results"] == {"query": "hello", "answers": ["a1", "a2"]}
        serialized = json.dumps(normalized, ensure_ascii=False)
        assert json.loads(serialized)["results"]["query"] == "hello"

    def test_root_pydantic_model_data_is_json_serializable(self, manager):
        """Same coercion for the pydantic BaseModel variant of a "Root" result."""
        raw = MagicMock()
        raw.data = _RootModel(query="hello", answers=["a1", "a2"])
        raw.structured_content = None
        raw.content = []

        normalized = manager._normalize_mcp_tool_result(raw)

        assert normalized["results"] == {"query": "hello", "answers": ["a1", "a2"]}
        json.dumps(normalized, ensure_ascii=False)  # must not raise

    def test_structured_content_with_nested_model_is_json_serializable(self, manager):
        """structured_content containing a nested model instance is coerced
        so json.dumps of the normalized result does not raise."""
        raw = MagicMock()
        raw.data = None
        raw.structured_content = {
            "results": "ok",
            "meta_data": {"detail": _RootModel(query="hello", answers=["a1"])},
        }
        raw.content = []

        normalized = manager._normalize_mcp_tool_result(raw)

        assert normalized["results"] == "ok"
        serialized = json.dumps(normalized, ensure_ascii=False)
        decoded = json.loads(serialized)
        assert decoded["meta_data"]["detail"] == {"query": "hello", "answers": ["a1"]}

    def test_extract_v2_components_from_root_data(self, manager):
        """_extract_v2_components must also coerce a "Root" data instance so
        artifacts/display/meta_data nested inside the object are surfaced."""
        raw = MagicMock()
        raw.data = _RootModelWithArtifacts(
            query="hello",
            answers=[],
            artifacts=[{"name": "out.png", "b64": "Zm9v", "mime": "image/png"}],
            display={"open_canvas": True, "primary_file": "out.png"},
            meta_data={"source": "freecad"},
        )
        raw.structured_content = None
        raw.content = []

        artifacts, display_config, meta_data = manager._extract_v2_components(
            raw, "obj_tool"
        )

        assert len(artifacts) == 1
        assert artifacts[0]["name"] == "out.png"
        assert display_config == {"open_canvas": True, "primary_file": "out.png"}
        assert meta_data == {"source": "freecad"}
