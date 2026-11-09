"""End-to-end checks for the team prompt library (issue #1169).

Drives a running Atlas server over its real REST API and WebSocket as several
identities (X-User-Email, honoured in debug mode as from a trusted proxy), and
reads the mock LLM's /test/last-request to prove which system prompt a chat
turn actually sent to the model.

Expects: owner@example.com and member@example.com in group "eng"
(AUTH_STATIC_GROUPS), outsider@example.com in no group, chat history and
custom prompts enabled, and the mock-model pointing at the mock LLM.

Usage: python library_e2e.py <atlas_base_url> <mock_llm_base_url>
"""

import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid

import websockets

BASE, MOCK = sys.argv[1].rstrip("/"), sys.argv[2].rstrip("/")
WS = BASE.replace("http", "ws", 1) + "/ws"
OWNER, MEMBER, OUTSIDER = "owner@example.com", "member@example.com", "outsider@example.com"
V1 = "SYSTEM-PROMPT-V1: review code for security issues first."
V2 = "SYSTEM-PROMPT-V2: review code for security and performance."
MODEL = "mock-model"

passed = failed = 0


def check(ok, label):
    global passed, failed
    if ok:
        passed += 1
        print(f"PASSED: {label}")
    else:
        failed += 1
        print(f"FAILED: {label}")


def api(method, path, user, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        headers={"X-User-Email": user, "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def system_prompt_sent():
    """The system prompt of the last model call (Atlas appends date/time to any prompt)."""
    return last_llm_request().get("system_prompt") or ""


def last_llm_request():
    with urllib.request.urlopen(MOCK + "/test/last-request", timeout=10) as r:
        return json.loads(r.read())


async def chat(user, conversation_id, library_prompt, extra=None):
    """One chat turn; returns ("ok", None) or ("error", frame)."""
    frame = {
        "type": "chat", "content": "Please review this function.", "model": MODEL,
        "save_mode": "server", "conversation_id": conversation_id, "selected_tools": [],
        **({"library_prompt": library_prompt} if library_prompt is not ... else {}),
        **(extra or {}),
    }
    async with websockets.connect(WS, additional_headers={"X-User-Email": user}) as ws:
        await ws.send(json.dumps(frame))
        async with asyncio.timeout(60):
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("type") == "error":
                    return "error", msg
                if msg.get("type") == "conversation_saved":
                    return "ok", msg


async def main():
    # --- Registry, privacy and sharing -----------------------------------
    s, body = api("POST", "/api/prompts", OWNER, {"title": "Security reviewer", "content": V1, "description": "e2e"})
    check(s == 200 and body["prompt"]["permission"] == "owner", "owner creates a private prompt")
    pid = body["prompt"]["id"]
    s, _ = api("GET", f"/api/prompts/{pid}", MEMBER)
    check(s == 404, "new prompt is private (member gets 404)")
    s, body = api("POST", f"/api/prompts/{pid}/publish", OWNER, {"change_description": "first"})
    v1 = body["version"]
    check(s == 200 and v1["version_number"] == 1, "owner publishes v1")
    s, body = api("PUT", f"/api/prompts/{pid}/sharing", OWNER, {"groups": ["hr"], "users": []})
    check(s == 403, "sharing into a group the owner is not in is refused")
    s, body = api("PUT", f"/api/prompts/{pid}/sharing", OWNER, {"groups": ["eng"], "users": []})
    check(s == 200 and body["sharing"]["groups"] == ["eng"], "owner shares with group eng")

    s, body = api("GET", "/api/prompts?scope=team", MEMBER)
    team = {p["id"]: p for p in body.get("prompts", [])}
    check(s == 200 and pid in team, "member sees it under Team")
    check(team.get(pid, {}).get("permission") == "use" and "sharing" not in team.get(pid, {}),
          "member has use permission and no sharing details")
    s, _ = api("PUT", f"/api/prompts/{pid}", MEMBER, {"content": "hijack"})
    check(s == 403, "group membership does not grant edit")
    s, body = api("GET", "/api/prompts?q=Security", OUTSIDER)
    check(s == 200 and body["prompts"] == [], "outsider search finds nothing")
    s, real = api("GET", f"/api/prompts/{pid}/versions/{v1['id']}", OUTSIDER)
    s2, ghost = api("GET", f"/api/prompts/{uuid.uuid4()}/versions/{v1['id']}", OUTSIDER)
    check(s == s2 == 404 and real == ghost, "outsider cannot tell a real id from a guessed one")

    # --- Server-side execution and pinning ---------------------------------
    conv = str(uuid.uuid4())
    status, _ = await chat(MEMBER, conv, {"prompt_id": pid, "version_id": None})
    check(status == "ok" and system_prompt_sent().startswith(V1),
          "member's chat runs v1, resolved server-side")
    s, stored = api("GET", f"/api/conversations/{conv}", MEMBER)
    pin = (stored.get("metadata") or {}).get("prompt_library") if s == 200 else None
    check(pin and pin["version_id"] == v1["id"], "conversation records the pinned version")

    api("PUT", f"/api/prompts/{pid}", OWNER, {"content": V2})
    s, body = api("POST", f"/api/prompts/{pid}/publish", OWNER, {"change_description": "second"})
    v2 = body["version"]
    check(s == 200 and v2["version_number"] == 2, "owner publishes v2")
    s, body = api("GET", f"/api/prompts/{pid}/versions/{v1['id']}", MEMBER)
    check(body["version"]["content"] == V1, "v1 is unchanged after v2 is published")

    status, _ = await chat(MEMBER, conv, {"prompt_id": pid, "version_id": None})
    check(status == "ok" and system_prompt_sent().startswith(V1),
          "existing conversation keeps v1 after v2 is published")
    status, _ = await chat(MEMBER, str(uuid.uuid4()), {"prompt_id": pid, "version_id": None})
    check(status == "ok" and system_prompt_sent().startswith(V2), "a new conversation runs v2")

    # --- Crafted frames ----------------------------------------------------
    before = last_llm_request().get("timestamp")
    for label, ref, extra in (
        ("outsider naming the shared prompt", {"prompt_id": pid, "version_id": None}, None),
        ("outsider naming a version id", {"prompt_id": pid, "version_id": v1["id"]}, None),
        ("outsider with inline text riding along", {"prompt_id": pid, "version_id": None},
         {"custom_system_prompt": "INJECTED"}),
    ):
        status, frame = await chat(OUTSIDER, str(uuid.uuid4()), ref, extra)
        check(status == "error" and frame.get("error_type") == "authorization" and V1 not in json.dumps(frame),
              f"crafted frame refused: {label}")
    check(last_llm_request().get("timestamp") == before, "no refused turn reached the model")

    # --- Revocation --------------------------------------------------------
    api("PUT", f"/api/prompts/{pid}/sharing", OWNER, {"groups": [], "users": []})
    status, frame = await chat(MEMBER, conv, ...)
    check(status == "error" and frame.get("error_type") == "authorization",
          "after unsharing, continuing the pinned conversation is refused")
    s, stored = api("GET", f"/api/conversations/{conv}", MEMBER)
    check(s == 200 and stored["metadata"]["prompt_library"]["version_id"] == v1["id"],
          "history keeps its pinned reference after revocation")
    status, _ = await chat(MEMBER, conv, None)
    check(status == "ok" and not system_prompt_sent().startswith(V1),
          "the member continues by explicitly choosing another prompt")

    # --- Fork and audit ----------------------------------------------------
    api("PUT", f"/api/prompts/{pid}/sharing", OWNER, {"groups": ["eng"], "users": []})
    s, body = api("POST", f"/api/prompts/{pid}/fork", MEMBER, {"version_id": v1["id"]})
    check(s == 200 and body["prompt"]["is_owner"] and body["prompt"]["draft_content"] == V1,
          "member forks v1 into a private copy")
    s, _ = api("GET", f"/api/prompts/{body['prompt']['id']}", OWNER)
    check(s == 404, "the fork is private to the member")
    s, body = api("GET", f"/api/prompts/{pid}/audit", OWNER)
    kinds = {e["event_type"] for e in body["events"]}
    check({"create", "publish", "share", "update_draft", "use", "use_denied", "fork"} <= kinds,
          "audit trail records create/publish/share/update/use/denied/fork")
    check("SYSTEM-PROMPT" not in json.dumps(body), "audit trail holds no prompt text")
    s, _ = api("GET", f"/api/prompts/{pid}/audit", MEMBER)
    check(s == 403, "audit trail needs manage permission")

    print(f"\nlibrary_e2e: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
