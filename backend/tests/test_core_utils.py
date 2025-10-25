import asyncio
from types import SimpleNamespace

import pytest

from core.utils import get_current_user


@pytest.mark.asyncio
async def test_get_current_user_default():
    class Dummy:
        pass
    req = SimpleNamespace(state=SimpleNamespace())
    assert await get_current_user(req) == "test@test.com"


@pytest.mark.asyncio
async def test_get_current_user_from_state():
    req = SimpleNamespace(state=SimpleNamespace(user_email="user@example.com"))
    assert await get_current_user(req) == "user@example.com"
