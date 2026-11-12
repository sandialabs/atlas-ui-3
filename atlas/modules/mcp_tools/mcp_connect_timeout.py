"""Bounded connection to an MCP client.

Entering a FastMCP ``Client`` (``async with client:``) spawns or dials the
server and runs the MCP initialize handshake, and by default nothing bounds
that wait. A stdio server whose process exits as soon as it is spawned (for
example its script is not in the image) usually fails at once with
"Connection closed", but the stdio transport can tear down while the
initialize request is still pending, and then the request is never answered.
During startup discovery that left the whole application waiting forever
before it served a single request.

``connected_client`` gives the connection attempt a deadline, so the caller
gets a ``TimeoutError`` instead of an indefinite wait.
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator


@asynccontextmanager
async def connected_client(client: Any, timeout: float) -> AsyncIterator[Any]:
    """Enter ``client`` within ``timeout`` seconds, then yield it.

    Only the connection attempt is bounded; the caller keeps its own timeouts
    on the calls it makes inside the block. A cancelled connection attempt is
    cleaned up by the client itself (it stops its session task and closes the
    transport), so the same client object can be connected again later.

    Raises:
        TimeoutError: the client did not connect in time.
    """
    try:
        await asyncio.wait_for(client.__aenter__(), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Timeout connecting to MCP server: no session after {timeout} seconds"
        ) from None
    try:
        yield client
    except BaseException as exc:
        if not await client.__aexit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        await client.__aexit__(None, None, None)
