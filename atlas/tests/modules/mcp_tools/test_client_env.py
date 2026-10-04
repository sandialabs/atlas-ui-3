import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from atlas.modules.mcp_tools.client import MCPToolManager


class TestMCPClientEnvironmentVariables:
    """Test MCP client initialization with environment variables."""

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_with_env_vars(self, mock_transport_class, mock_client_class, monkeypatch):
        """Should pass environment variables to StdioTransport."""
        # Set up environment variables for resolution
        monkeypatch.setenv("MY_ENV_VAR", "resolved-value")

        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend",
            "env": {
                "VAR1": "literal-value",
                "VAR2": "another-literal"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                await manager._initialize_single_client("test-server", server_config)

        # Verify StdioTransport was called with env dict
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        assert "env" in call_kwargs
        env = call_kwargs["env"]
        assert env["VAR1"] == "literal-value"
        assert env["VAR2"] == "another-literal"
        # PYTHONPATH is injected so STDIO servers can import atlas.mcp_shared
        assert "PYTHONPATH" in env

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_with_env_var_resolution(self, mock_transport_class, mock_client_class, monkeypatch):
        """Should resolve ${ENV_VAR} patterns in env values."""
        # Set up environment variables
        monkeypatch.setenv("CLOUD_PROFILE", "my-profile-9")
        monkeypatch.setenv("CLOUD_REGION", "us-east-7")

        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend",
            "env": {
                "PROFILE": "${CLOUD_PROFILE}",
                "REGION": "${CLOUD_REGION}",
                "LITERAL": "not-a-var"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                await manager._initialize_single_client("test-server", server_config)

        # Verify env vars were resolved
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        env = call_kwargs["env"]
        assert env["PROFILE"] == "my-profile-9"
        assert env["REGION"] == "us-east-7"
        assert env["LITERAL"] == "not-a-var"
        assert "PYTHONPATH" in env

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_without_env(self, mock_transport_class, mock_client_class):
        """Should pass an explicit child environment when no env is specified."""
        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend"
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                await manager._initialize_single_client("test-server", server_config)

        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        env = call_kwargs["env"]
        assert env is not None
        assert "PYTHONPATH" in env

    @pytest.mark.asyncio
    async def test_stdio_client_missing_env_var_fails(self, caplog):
        """Should fail when env var resolution fails."""
        server_config = {
            "command": ["python", "server.py"],
            "cwd": "backend",
            "env": {
                "PROFILE": "${MISSING_VAR}"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            # Mock os.path.exists to return True for cwd
            with patch('os.path.exists', return_value=True):
                manager = MCPToolManager()
                manager.servers_config = {"test-server": server_config}

                result = await manager._initialize_single_client("test-server", server_config)

        # Should return None and log error
        assert result is None
        assert "Failed to resolve env var" in caplog.text
        assert "MISSING_VAR" in caplog.text

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_with_env_no_cwd(self, mock_transport_class, mock_client_class, monkeypatch):
        """Should pass env vars even when no cwd specified."""
        monkeypatch.setenv("MY_VAR", "my-value")

        server_config = {
            "command": ["python", "server.py"],
            "env": {
                "TEST_VAR": "${MY_VAR}"
            }
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            await manager._initialize_single_client("test-server", server_config)

        # Verify env was passed
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        env = call_kwargs["env"]
        assert env["TEST_VAR"] == "my-value"
        assert "PYTHONPATH" in env

    @pytest.mark.asyncio
    @patch('atlas.modules.mcp_tools.client.Client')
    @patch('fastmcp.client.transports.StdioTransport')
    async def test_stdio_client_empty_env_dict(self, mock_transport_class, mock_client_class):
        """Should handle empty env dict."""
        server_config = {
            "command": ["python", "server.py"],
            "env": {}
        }

        with patch('atlas.modules.mcp_tools.client.config_manager') as mock_config_manager:
            mock_config_manager.mcp_config.servers = {"test-server": Mock()}
            mock_config_manager.mcp_config.servers["test-server"].model_dump.return_value = server_config

            manager = MCPToolManager()
            manager.servers_config = {"test-server": server_config}

            await manager._initialize_single_client("test-server", server_config)

        # Empty env dict still gets PYTHONPATH injected
        assert mock_transport_class.called
        call_kwargs = mock_transport_class.call_args[1]
        env = call_kwargs["env"]
        assert "PYTHONPATH" in env


@pytest.mark.asyncio
@pytest.mark.parametrize("env_config", [{}, {"env": None}, {"env": {}}])
@pytest.mark.parametrize("command_config", [
    {"command": ["python", "server.py"], "cwd": "atlas"},
    {"command": ["python3", "server.py"]},
    {},
])
async def test_stdio_environment_is_allowlisted(env_config, command_config, monkeypatch):
    """All stdio paths exclude undeclared backend values, including empty env."""
    private_names = (
        "CAPABILITY_TOKEN_SECRET", "PROXY_SECRET", "MCP_TOKEN_ENCRYPTION_KEY",
        "OPENAI_API_KEY", "AWS_ACCESS_KEY_ID", "CUSTOM_BACKEND_VALUE",
        "LD_PRELOAD", "VIRTUAL_ENV", "LC_TEST_SECRET",
    )
    for key in private_names:
        monkeypatch.setenv(key, "test-only-value")
    monkeypatch.setenv("HOME", "/home/stdio-test")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_CTYPE", "C.UTF-8")
    monkeypatch.setenv("PATH", "/backend/private/bin")
    monkeypatch.setenv("PYTHONPATH", "/backend/private/modules")
    config = {**command_config, **env_config}

    with (
        patch("atlas.modules.mcp_tools.client.config_manager"),
        patch("atlas.modules.mcp_tools.client.Client") as client_class,
        patch("fastmcp.client.transports.StdioTransport") as transport_class,
        patch("os.path.exists", return_value=True),
    ):
        manager = MCPToolManager()
        manager.servers_config = {"test-server": config}
        assert await manager._initialize_single_client("test-server", config) is not None

    kwargs = transport_class.call_args.kwargs
    env = kwargs["env"]
    assert not set(private_names).intersection(env)
    assert env["HOME"] == "/home/stdio-test"
    assert env["LANG"] == env["LC_CTYPE"] == "C.UTF-8"
    assert env["PATH"] == "/usr/local/bin:/usr/bin:/bin"
    project_root = Path(__file__).resolve().parents[4]
    assert env["PYTHONPATH"] == str(project_root)
    assert kwargs["command"] == sys.executable
    assert kwargs["args"] == (
        ["server.py"] if command_config else [str(Path("mcp/test-server/main.py").resolve())]
    )
    if "cwd" in command_config:
        assert kwargs["cwd"] == str(project_root / "atlas")
    client_class.assert_called_once()
    assert client_class.call_args.args[0] is transport_class.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("command_config", [
    {"command": ["/custom/python", "server.py"]},
    {},
])
async def test_stdio_trusted_env_overrides_baseline(command_config, monkeypatch):
    """Explicit secrets and import paths survive the Portal baseline denylist."""
    monkeypatch.setenv("MCP_TEST_DECLARED_SECRET", "test-only-declared-value")
    config = {
        **command_config,
        "env": {
            "OPENAI_API_KEY": "${MCP_TEST_DECLARED_SECRET}",
            "AWS_SECRET_ACCESS_KEY": "test-only-literal",
            "PATH": "/custom/bin",
            "HOME": "/custom/home",
            "PYTHONPATH": "/custom/modules",
            "EMPTY_VALUE": "",
        },
    }
    with (
        patch("atlas.modules.mcp_tools.client.config_manager"),
        patch("atlas.modules.mcp_tools.client.Client"),
        patch("fastmcp.client.transports.StdioTransport") as transport_class,
        patch("os.path.exists", return_value=True),
    ):
        manager = MCPToolManager()
        manager.servers_config = {"test-server": config}
        assert await manager._initialize_single_client("test-server", config) is not None

    kwargs = transport_class.call_args.kwargs
    env = kwargs["env"]
    assert env["OPENAI_API_KEY"] == "test-only-declared-value"
    assert env["AWS_SECRET_ACCESS_KEY"] == "test-only-literal"
    assert env["PATH"] == "/custom/bin"
    assert env["HOME"] == "/custom/home"
    assert env["EMPTY_VALUE"] == ""
    assert env["PYTHONPATH"].split(os.pathsep) == [
        str(Path(__file__).resolve().parents[4]), "/custom/modules",
    ]
    assert kwargs["command"] == ("/custom/python" if command_config else sys.executable)
    assert "MCP_TEST_DECLARED_SECRET" not in env


@pytest.mark.asyncio
async def test_implicit_stdio_missing_env_fails_before_client_creation(monkeypatch):
    monkeypatch.delenv("MCP_TEST_UNDEFINED_VARIABLE", raising=False)
    config = {"env": {"API_KEY": "${MCP_TEST_UNDEFINED_VARIABLE}"}}
    with (
        patch("atlas.modules.mcp_tools.client.config_manager"),
        patch("atlas.modules.mcp_tools.client.Client") as client_class,
    ):
        manager = MCPToolManager()
        manager.servers_config = {"test-server": config}
        assert await manager._initialize_single_client("test-server", config) is None
        client_class.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("implicit_script", [False, True])
async def test_real_stdio_child_receives_only_declared_secret(tmp_path, monkeypatch, implicit_script):
    """Exercise the actual FastMCP/SDK spawn, not just transport construction."""
    monkeypatch.setenv("MCP_TEST_PRIVATE_VALUE", "test-only-private-value")
    monkeypatch.setenv("MCP_TEST_SOURCE_SECRET", "test-only-declared-value")
    monkeypatch.chdir(tmp_path)
    script_dir = tmp_path / "mcp" / "test-server"
    script_dir.mkdir(parents=True)
    script = script_dir / "main.py"
    script.write_text(
        "import os\n"
        "import sys\n"
        "import atlas.mcp_shared\n"
        "from fastmcp import FastMCP\n"
        "server = FastMCP('environment-test')\n"
        "@server.tool\n"
        "def environment() -> dict:\n"
        "    return {\n"
        "        'private': os.environ.get('MCP_TEST_PRIVATE_VALUE'),\n"
        "        'source': os.environ.get('MCP_TEST_SOURCE_SECRET'),\n"
        "        'declared': os.environ.get('MCP_TEST_API_KEY'),\n"
        "        'cwd': os.getcwd(),\n"
        "        'python': sys.executable,\n"
        "    }\n"
        "server.run(show_banner=False)\n",
        encoding="utf-8",
    )
    config = {"env": {"MCP_TEST_API_KEY": "${MCP_TEST_SOURCE_SECRET}"}}
    if not implicit_script:
        config.update(command=["python", str(script)], cwd=str(tmp_path))
    with patch("atlas.modules.mcp_tools.client.config_manager"):
        manager = MCPToolManager()
        manager.servers_config = {"test-server": config}
        client = await manager._initialize_single_client("test-server", config)
    assert client is not None
    try:
        async with client:
            result = await client.call_tool("environment")
        assert result.data == {
            "private": None,
            "source": None,
            "declared": "test-only-declared-value",
            "cwd": str(tmp_path),
            "python": sys.executable,
        }
    finally:
        await client.close()
