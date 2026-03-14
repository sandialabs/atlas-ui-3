"""Tests for BlockedStateStore — ensures STDIO servers cannot use session state."""

import pytest

from atlas.mcp.shared.blocked_state import BlockedStateStore

MSG_FRAGMENT = "not supported for STDIO"


@pytest.fixture
def store():
    return BlockedStateStore()


@pytest.mark.asyncio
async def test_get_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.get("key")


@pytest.mark.asyncio
async def test_put_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.put("key", {"value": 1})


@pytest.mark.asyncio
async def test_delete_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.delete("key")


@pytest.mark.asyncio
async def test_ttl_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.ttl("key")


@pytest.mark.asyncio
async def test_get_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.get_many(["a", "b"])


@pytest.mark.asyncio
async def test_put_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.put_many(["a"], [{"v": 1}])


@pytest.mark.asyncio
async def test_delete_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.delete_many(["a"])


@pytest.mark.asyncio
async def test_ttl_many_raises(store):
    with pytest.raises(RuntimeError, match=MSG_FRAGMENT):
        await store.ttl_many(["a"])
