"""Tests for structured output parsing priority."""
import json
import pytest
from unittest.mock import MagicMock

from atlas.modules.mcp_tools.client import MCPToolManager


@pytest.fixture
def manager():
    """Create a MCPToolManager for testing normalization."""
    return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")


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
