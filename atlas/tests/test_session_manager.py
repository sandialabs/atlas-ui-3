"""Tests for MCPSessionManager."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.modules.mcp_tools.session_manager import (
    ManagedSession,
    MCPSessionManager,
)


@pytest.fixture
def session_manager():
    return MCPSessionManager()


@pytest.fixture
def mock_client():
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.call_tool = AsyncMock(return_value=MagicMock())
    client.is_connected = MagicMock(return_value=True)
    return client


class TestMCPSessionManager:
    @pytest.mark.asyncio
    async def test_acquire_creates_new_session(self, session_manager, mock_client):
        session = await session_manager.acquire("conv-1", "server-a", mock_client)
        assert session is not None
        assert isinstance(session, ManagedSession)
        mock_client.__aenter__.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_reuses_existing_session(self, session_manager, mock_client):
        session1 = await session_manager.acquire("conv-1", "server-a", mock_client)
        session2 = await session_manager.acquire("conv-1", "server-a", mock_client)
        assert session1 is session2
        mock_client.__aenter__.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_different_conversations_get_different_sessions(self, session_manager):
        client1 = AsyncMock()
        client1.__aenter__ = AsyncMock(return_value=client1)
        client1.__aexit__ = AsyncMock(return_value=False)

        client2 = AsyncMock()
        client2.__aenter__ = AsyncMock(return_value=client2)
        client2.__aexit__ = AsyncMock(return_value=False)

        s1 = await session_manager.acquire("conv-1", "server-a", client1)
        s2 = await session_manager.acquire("conv-2", "server-a", client2)
        assert s1 is not s2

    @pytest.mark.asyncio
    async def test_release_closes_session(self, session_manager, mock_client):
        await session_manager.acquire("conv-1", "server-a", mock_client)
        await session_manager.release("conv-1", "server-a")
        mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_all_closes_all_sessions_for_conversation(self, session_manager):
        clients = []
        for name in ["server-a", "server-b", "server-c"]:
            c = AsyncMock()
            c.__aenter__ = AsyncMock(return_value=c)
            c.__aexit__ = AsyncMock(return_value=False)
            clients.append(c)
            await session_manager.acquire("conv-1", name, c)

        await session_manager.release_all("conv-1")
        for c in clients:
            c.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_release_all_does_not_affect_other_conversations(self, session_manager):
        client_conv1 = AsyncMock()
        client_conv1.__aenter__ = AsyncMock(return_value=client_conv1)
        client_conv1.__aexit__ = AsyncMock(return_value=False)

        client_conv2 = AsyncMock()
        client_conv2.__aenter__ = AsyncMock(return_value=client_conv2)
        client_conv2.__aexit__ = AsyncMock(return_value=False)

        await session_manager.acquire("conv-1", "server-a", client_conv1)
        await session_manager.acquire("conv-2", "server-a", client_conv2)

        await session_manager.release_all("conv-1")
        client_conv1.__aexit__.assert_called_once()
        client_conv2.__aexit__.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_nonexistent_is_noop(self, session_manager):
        await session_manager.release("nonexistent", "server-a")
        await session_manager.release_all("nonexistent")

    @pytest.mark.asyncio
    async def test_acquire_after_release_creates_new_session(self, session_manager, mock_client):
        s1 = await session_manager.acquire("conv-1", "server-a", mock_client)
        await session_manager.release("conv-1", "server-a")

        mock_client.__aenter__.reset_mock()
        s2 = await session_manager.acquire("conv-1", "server-a", mock_client)
        assert s2 is not s1
        mock_client.__aenter__.assert_called_once()


class TestManagedSession:
    @pytest.mark.asyncio
    async def test_client_property(self, mock_client):
        session = ManagedSession(mock_client)
        await session.open()
        assert session.client is mock_client

    @pytest.mark.asyncio
    async def test_close_calls_aexit(self, mock_client):
        session = ManagedSession(mock_client)
        await session.open()
        await session.close()
        mock_client.__aexit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, mock_client):
        session = ManagedSession(mock_client)
        await session.open()
        await session.close()
        await session.close()
        assert mock_client.__aexit__.call_count == 1
