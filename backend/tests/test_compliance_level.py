"""Test compliance level functionality for MCP servers and data sources."""

from backend.modules.config.config_manager import MCPServerConfig, MCPConfig


def test_mcp_server_config_with_compliance_level():
    """Test that MCPServerConfig accepts and stores compliance_level."""
    config = MCPServerConfig(
        description="Test server",
        compliance_level="SOC2"
    )
    assert config.compliance_level == "SOC2"


def test_mcp_server_config_without_compliance_level():
    """Test that MCPServerConfig works without compliance_level (backward compatible)."""
    config = MCPServerConfig(
        description="Test server"
    )
    assert config.compliance_level is None


def test_mcp_config_from_dict_with_compliance():
    """Test that MCPConfig properly parses servers with compliance levels."""
    data = {
        "servers": {
            "test_server": {
                "description": "Test description",
                "compliance_level": "HIPAA"
            }
        }
    }
    config = MCPConfig(**data)
    assert "test_server" in config.servers
    assert config.servers["test_server"].compliance_level == "HIPAA"


def test_compliance_level_in_config_response():
    """Test that /api/config returns compliance_level in tools response."""
    # This is an integration test that would require full app setup
    # For now, we verify the model supports it
    config_dict = {
        "description": "PDF processor",
        "author": "Test",
        "compliance_level": "SOC2",
        "groups": ["users"],
        "is_exclusive": False,
        "enabled": True
    }
    server_config = MCPServerConfig(**config_dict)
    
    # Verify it can be serialized to dict (as done in API responses)
    as_dict = server_config.model_dump()
    assert as_dict["compliance_level"] == "SOC2"
    assert as_dict["description"] == "PDF processor"
