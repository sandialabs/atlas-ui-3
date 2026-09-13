"""End-to-end validation for PR #936 (issue #935).

Drives a REAL FastMCP streamable-HTTP server behind a bearer-token gate and a
REAL (localhost) OAuth provider that rotates refresh tokens and retires the
previous access token on every refresh, then exercises MCPToolManager.call_tool
and mcp_oauth_service.refresh_stored_token through the production code paths:

1. A tool call succeeds and its client is cached for conversation c1 and c2.
2. A silent refresh driven "from elsewhere" (the discovery-sweep shape:
   refresh_stored_token with an expired stored token) rotates the access
   token. Both cached clients must be invalidated; the next call from c1 must
   succeed WITHOUT any 401 (the rotation-aware cache rebuilt the client).
3. Provider-side revocation with a still-valid-by-the-clock stored token: the
   tool call takes exactly one 401, then the forced refresh + retry heal it.
4. When refresh itself refuses (rotated refresh token), the retry is also
   refused and the caller receives AuthenticationRequiredException with a
   friendly message and no upstream URL.
"""

import asyncio
import json
import sys
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

USER = "v@example.com"
SERVER = "rot-server"

MCP_STATE = {"active_token": "seed-access", "unauthorized_seen": 0, "unauthorized_posts": 0}
PROVIDER_STATE = {
    "current_refresh": "seed-refresh",
    "issued": 0,
    "accept_refresh": True,
    "token_requests": [],
    "phase": "setup",
}


# --- the MCP server (FastMCP behind a bearer gate) -------------------------

from fastmcp import FastMCP

mcp = FastMCP("rot-server", tasks=None)


@mcp.tool
def echo(text: str) -> str:
    return f"echo:{text}"


mcp_app = mcp.http_app(path="/mcp")


async def protected_resource_metadata(request: Request) -> Response:
    provider = PROVIDER_URL[0]
    return JSONResponse({
        "resource": f"http://127.0.0.1:{MCP_PORT[0]}/mcp",
        "authorization_servers": [provider],
    })


class BearerGate:
    """Raw-ASGI bearer gate in front of the FastMCP app.

    Only the currently active access token passes; anything else gets a
    RFC 9728-shaped 401 challenge.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            from starlette.datastructures import Headers

            headers = Headers(scope=scope)
            auth = headers.get("authorization", "")
            expected = f"Bearer {MCP_STATE['active_token']}"
            if auth != expected:
                MCP_STATE["unauthorized_seen"] += 1
                if scope.get("method") == "POST":
                    # POSTs are real MCP traffic (initialize / call); DELETEs
                    # are session termination, whose 401 with a stale token is
                    # a harmless teardown artifact.
                    MCP_STATE["unauthorized_posts"] += 1
                challenge = Response(
                    status_code=401,
                    headers={
                        "WWW-Authenticate": (
                            'Bearer resource_metadata="http://127.0.0.1:%d/'
                            '.well-known/oauth-protected-resource/mcp"' % MCP_PORT[0]
                        )
                    },
                )
                await challenge(scope, receive, send)
                return
        await self.app(scope, receive, send)


# --- the OAuth provider (rotates refresh tokens, retires old access tokens) --

async def as_metadata(request: Request) -> Response:
    base = PROVIDER_URL[0]
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
    })


async def token_endpoint(request: Request) -> Response:
    form = await request.form()
    grant = form.get("grant_type")
    presented = form.get("refresh_token")
    PROVIDER_STATE["token_requests"].append(
        (PROVIDER_STATE["phase"], grant, presented, PROVIDER_STATE["current_refresh"])
    )
    if grant != "refresh_token":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    if form.get("client_id") != "atlas-test-client":
        return JSONResponse({"error": "invalid_client"}, status_code=401)
    if not PROVIDER_STATE["accept_refresh"] or presented != PROVIDER_STATE["current_refresh"]:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    PROVIDER_STATE["issued"] += 1
    access = f"access-v{PROVIDER_STATE['issued']}"
    refresh = f"refresh-v{PROVIDER_STATE['issued']}"
    PROVIDER_STATE["current_refresh"] = refresh
    # Rotation semantics: the previous access token is retired the moment a
    # new one is issued.
    MCP_STATE["active_token"] = access
    return JSONResponse({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": refresh,
    })


# --- wiring ----------------------------------------------------------------

PROVIDER_URL = [None]
MCP_PORT = [None]


def build_mcp_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata),
            Route("/.well-known/oauth-protected-resource", protected_resource_metadata),
            Mount("/", app=BearerGate(mcp_app)),
        ],
        lifespan=mcp_app.router.lifespan_context,
    )


def build_provider_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/.well-known/oauth-authorization-server", as_metadata),
            Route("/token", token_endpoint, methods=["POST"]),
        ],
    )


def serve(app: Starlette):
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, port


def write_mcp_config(path: str, mcp_port: int) -> None:
    with open(path, "w") as fh:
        json.dump({
            SERVER: {
                "url": f"http://127.0.0.1:{mcp_port}/mcp",
                "transport": "http",
                "auth_type": "oauth",
                "oauth_config": {"client_id": "atlas-test-client"},
                "groups": [],
            }
        }, fh)


PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        print(f"PASSED: {name}")
        PASSED += 1
    else:
        print(f"FAILED: {name} {detail}")
        FAILED += 1


async def main() -> None:
    import tempfile
    from pathlib import Path

    from atlas.modules.mcp_tools import mcp_oauth_service
    from atlas.modules.mcp_tools.client import MCPToolManager
    from atlas.modules.mcp_tools.mcp_oauth import clear_metadata_cache
    from atlas.modules.mcp_tools.token_storage import (
        AuthenticationRequiredException,
        get_token_storage,
    )

    _, provider_port = serve(build_provider_app())
    PROVIDER_URL[0] = f"http://127.0.0.1:{provider_port}"
    _, mcp_port = serve(build_mcp_app())
    MCP_PORT[0] = mcp_port

    with tempfile.TemporaryDirectory() as tmp:
        config_path = str(Path(tmp) / "mcp.json")
        write_mcp_config(config_path, mcp_port)
        manager = MCPToolManager(config_path=config_path)
        # This scenario is about auth recovery, not task-mode fallback: skip
        # the task-augmented attempt so the bearer gate sees plain calls.
        manager._tool_task_forbidden.add((SERVER, "echo"))
        # Mirror production wiring: the OAuth service evicts clients through
        # the app-factory singleton, so register this manager as the factory's.
        # Note: atlas/infrastructure/__init__.py re-exports the singleton
        # instance under the submodule name, so import the attribute directly.
        from atlas.infrastructure.app_factory import app_factory as app_factory_instance

        app_factory_instance.mcp_tools = manager

        storage = get_token_storage()
        clear_metadata_cache()

        now = time.time()
        # Seed with values outside the provider's issued numbering so a
        # rotation always changes the token value.
        storage.store_token(
            user_email=USER,
            server_name=SERVER,
            token_value="seed-access",
            token_type="oauth_access",
            expires_at=now + 3600,
            refresh_token="seed-refresh",
            metadata={"source": "mcp_oauth_flow"},
        )
        PROVIDER_STATE["current_refresh"] = "seed-refresh"
        MCP_STATE["active_token"] = "seed-access"

        # 1. Baseline: one call per conversation, each client cached.
        r1 = await manager.call_tool(SERVER, "echo", {"text": "hi c1"}, user_email=USER, conversation_id="c1")
        check("initial call from c1 succeeds", r1 is not None)
        r2 = await manager.call_tool(SERVER, "echo", {"text": "hi c2"}, user_email=USER, conversation_id="c2")
        check("initial call from c2 succeeds", r2 is not None)
        check(
            "clients cached for both conversations",
            (USER, SERVER, "c1") in manager._user_clients and (USER, SERVER, "c2") in manager._user_clients,
        )

        # 2. A silent refresh from "elsewhere" (the discovery sweep shape)
        #    rotates the token: expire the stored record, then refresh.
        PROVIDER_STATE["phase"] = "silent-rotation"
        expired = storage.get_token(USER, SERVER)
        storage.store_token(
            user_email=USER,
            server_name=SERVER,
            token_value=expired.token_value,
            token_type=expired.token_type,
            expires_at=now - 1,
            refresh_token=expired.refresh_token,
        )
        rotated = await mcp_oauth_service.refresh_stored_token(USER, SERVER, manager.servers_config[SERVER])
        check("silent refresh rotated the token", rotated is not None and rotated.token_value == "access-v1")
        check("the provider retired the previous token", MCP_STATE["active_token"] == "access-v1")
        # Entries touched within the in-use window stay cached (a streaming
        # call elsewhere must not be torn down); they are now fingerprint-stale.
        stale_entry = manager._user_clients.get((USER, SERVER, "c1"))
        check(
            "in-use cache entries survive the refresh (no mid-call teardown)",
            stale_entry is not None,
        )
        unauthorized_before = MCP_STATE["unauthorized_posts"]
        client_before = stale_entry
        r3 = await manager.call_tool(SERVER, "echo", {"text": "after rotation"}, user_email=USER, conversation_id="c1")
        check("call after rotation succeeds", r3 is not None)
        check(
            "no MCP call was refused after rotation (cache rebuilt, not replayed)",
            MCP_STATE["unauthorized_posts"] == unauthorized_before,
        )
        check(
            "the cached client was rebuilt with the rotated token",
            manager._user_clients.get((USER, SERVER, "c1")) is not client_before,
        )

        # 3. Provider-side revocation with a valid-by-the-clock stored token.
        PROVIDER_STATE["phase"] = "self-heal"
        MCP_STATE["active_token"] = "access-revoked"
        issued_before = PROVIDER_STATE["issued"]
        r4 = await manager.call_tool(SERVER, "echo", {"text": "self-heal"}, user_email=USER, conversation_id="c1")
        check("401 from a revoked token self-heals to a success", r4 is not None)
        check(
            "the recovery ran exactly one forced refresh (single retry)",
            PROVIDER_STATE["issued"] == issued_before + 1,
            detail=f"provider issued {PROVIDER_STATE['issued'] - issued_before} token(s)",
        )
        check("the healed call used the freshly issued token", MCP_STATE["active_token"] == f"access-v{PROVIDER_STATE['issued']}")

        # 4. Refresh refuses (rotated refresh token) and the retry is refused
        #    too: friendly AuthenticationRequiredException, no upstream URL.
        PROVIDER_STATE["phase"] = "exhausted"
        PROVIDER_STATE["accept_refresh"] = False
        MCP_STATE["active_token"] = "access-stranger"
        issued_before = PROVIDER_STATE["issued"]
        try:
            await manager.call_tool(SERVER, "echo", {"text": "doomed"}, user_email=USER, conversation_id="c1")
            check("exhausted 401 raises AuthenticationRequiredException", False, detail="no exception raised")
        except AuthenticationRequiredException as exc:
            message = str(exc)
            check("exhausted 401 raises AuthenticationRequiredException", True)
            check("message names 401 and re-authorization", "401" in message and "re-authorized" in message, detail=message)
            check("no upstream URL leaks into the message", "127.0.0.1" not in message, detail=message)
            check(
                "oauth servers get a reconnect URL",
                exc.oauth_start_url == f"/api/mcp/auth/{SERVER}/oauth/start",
            )
        check(
            "the refused refresh issued no new token",
            PROVIDER_STATE["issued"] == issued_before,
        )

    await manager.cleanup()
    print("token endpoint requests (phase, grant, presented, expected):")
    for entry in PROVIDER_STATE["token_requests"]:
        print("   ", entry)


if __name__ == "__main__":
    asyncio.run(main())
    print()
    print(f"Passed: {PASSED} | Failed: {FAILED}")
    sys.exit(0 if FAILED == 0 else 1)