"""Tests for BlockedStateStore — ensures STDIO servers cannot store session state.

Read operations return empty values (safe). Write operations raise RuntimeError.
"""

import pytest

from atlas.mcp_shared.blocked_state import BlockedStateStore

WRITE_FRAGMENT = "not supported for STDIO"


@pytest.fixture
def store():
    return BlockedStateStore()


# --- Read operations return empty values ---

@pytest.mark.asyncio
async def test_get_returns_none(store):
    assert await store.get("key") is None


@pytest.mark.asyncio
async def test_get_many_returns_nones(store):
    result = await store.get_many(["a", "b"])
    assert result == [None, None]


@pytest.mark.asyncio
async def test_ttl_returns_none_tuple(store):
    assert await store.ttl("key") == (None, None)


@pytest.mark.asyncio
async def test_ttl_many_returns_none_tuples(store):
    result = await store.ttl_many(["a", "b"])
    assert result == [(None, None), (None, None)]


# --- Delete operations are no-ops ---

@pytest.mark.asyncio
async def test_delete_returns_false(store):
    assert await store.delete("key") is False


@pytest.mark.asyncio
async def test_delete_many_returns_zero(store):
    assert await store.delete_many(["a"]) == 0


# --- Write operations raise ---

@pytest.mark.asyncio
async def test_put_raises(store):
    with pytest.raises(RuntimeError, match=WRITE_FRAGMENT):
        await store.put("key", {"value": 1})


@pytest.mark.asyncio
async def test_put_many_raises(store):
    with pytest.raises(RuntimeError, match=WRITE_FRAGMENT):
        await store.put_many(["a"], [{"v": 1}])
