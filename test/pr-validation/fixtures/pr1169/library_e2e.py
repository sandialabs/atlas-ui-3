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


def draft_revision(pid, user=None):
    return api("GET", f"/api/prompts/{pid}", user or OWNER)[1]["prompt"]["draft_revision"]


def share(pid, groups, users=(), user=None):
    revision = api("GET", f"/api/prompts/{pid}/sharing", user or OWNER)[1]["sharing"]["revision"]
    return api("PUT", f"/api/prompts/{pid}/sharing", user or OWNER,
               {"groups": list(groups), "users": list(users), "expected_revision": revision})


async def turn_on(ws, conversation_id, library_prompt, extra=None):
    """One chat turn on an open socket; returns ("ok", frame) or ("error", frame)."""
    frame = {
        "type": "chat", "content": "Please review this function.", "model": MODEL,
        "save_mode": "server", "conversation_id": conversation_id, "selected_tools": [],
        **({"library_prompt": library_prompt} if library_prompt is not ... else {}),
        **(extra or {}),
    }
    await ws.send(json.dumps(frame))
    async with asyncio.timeout(60):
        while True:
            msg = json.loads(await ws.recv())
            if msg.get("type") == "error":
                return "error", msg
            if msg.get("type") == "conversation_saved":
                return "ok", msg


async def chat(user, conversation_id, library_prompt, extra=None):
    """One chat turn on a fresh connection (a new server-side session)."""
    async with websockets.connect(WS, additional_headers={"X-User-Email": user}) as ws:
        return await turn_on(ws, conversation_id, library_prompt, extra)


def audit_count(pid, kind):
    return sum(1 for e in api("GET", f"/api/prompts/{pid}/audit", OWNER)[1]["events"] if e["event_type"] == kind)


async def main():
    # --- Registry, privacy and sharing -----------------------------------
    s, body = api("POST", "/api/prompts", OWNER, {"title": "Security reviewer", "content": V1, "description": "e2e"})
    check(s == 200 and body["prompt"]["permission"] == "owner", "owner creates a private prompt")
    pid = body["prompt"]["id"]
    s, _ = api("GET", f"/api/prompts/{pid}", MEMBER)
    check(s == 404, "new prompt is private (member gets 404)")
    s, body = api("POST", f"/api/prompts/{pid}/publish", OWNER,
                  {"change_description": "first", "expected_draft_revision": draft_revision(pid)})
    v1 = body["version"]
    check(s == 200 and v1["version_number"] == 1, "owner publishes v1")
    s, body = share(pid, ["hr"])
    check(s == 403, "sharing into a group the owner is not in is refused")
    s, body = share(pid, ["Users"])
    check(s == 400, "sharing with the everyone group is refused")
    s, body = share(pid, ["eng"])
    check(s == 200 and body["sharing"]["groups"] == ["eng"], "owner shares with group eng")
    s_groups, targets = api("GET", "/api/prompts/groups", OWNER)
    check(s_groups == 200 and targets == {"groups": ["eng", "ops"], "incomplete": False},
          "sharing targets are the owner's checked groups, reported complete")
    s, body = api("PUT", f"/api/prompts/{pid}/sharing", OWNER,
                  {"groups": [], "users": [], "expected_revision": body["sharing"]["revision"] - 1})
    check(s == 409, "a sharing change based on a stale revision is refused")

    s, body = api("GET", "/api/prompts?scope=team", MEMBER)
    team = {p["id"]: p for p in body.get("prompts", [])}
    check(s == 200 and pid in team, "member sees it under Team")
    check(team.get(pid, {}).get("permission") == "use" and "sharing" not in team.get(pid, {}),
          "member has use permission and no sharing details")
    s, _ = api("PUT", f"/api/prompts/{pid}", MEMBER, {"content": "hijack", "expected_draft_revision": 1})
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

    reviewed = draft_revision(pid)
    api("PUT", f"/api/prompts/{pid}", OWNER, {"content": V2, "expected_draft_revision": reviewed})
    s, _ = api("PUT", f"/api/prompts/{pid}", OWNER, {"content": "stale editor", "expected_draft_revision": reviewed})
    check(s == 409, "a stale draft save is refused")
    s, _ = api("POST", f"/api/prompts/{pid}/publish", OWNER, {"expected_draft_revision": reviewed})
    check(s == 409, "publishing a draft revision nobody reviewed is refused")
    s, body = api("POST", f"/api/prompts/{pid}/publish", OWNER,
                  {"change_description": "second", "expected_draft_revision": draft_revision(pid)})
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
    ):
        status, frame = await chat(OUTSIDER, str(uuid.uuid4()), ref, extra)
        check(status == "error" and frame.get("error_type") == "authorization" and V1 not in json.dumps(frame),
              f"crafted frame refused: {label}")

    # --- Conflicting prompts -----------------------------------------------
    uses, denials = audit_count(pid, "use"), audit_count(pid, "use_denied")
    for label, who, extra in (
        ("member, inline text", MEMBER, {"custom_system_prompt": "INJECTED"}),
        ("member, persona", MEMBER, {"persona_id": "any-persona"}),
        ("outsider, inline text", OUTSIDER, {"custom_system_prompt": "INJECTED"}),
    ):
        status, frame = await chat(who, str(uuid.uuid4()), {"prompt_id": pid, "version_id": None}, extra)
        check(status == "error" and frame.get("error_type") == "validation"
              and "not both" in frame.get("message", "") and V1 not in json.dumps(frame),
              f"a team prompt plus another prompt is rejected ({label})")
    check(audit_count(pid, "use") == uses and audit_count(pid, "use_denied") == denials,
          "a rejected conflicting frame audits no use or denial")
    check(last_llm_request().get("timestamp") == before, "no refused turn reached the model")

    # --- Clearing a pin binds a session that is already open ---------------
    live = str(uuid.uuid4())
    async with websockets.connect(WS, additional_headers={"X-User-Email": MEMBER}) as ws:
        status, _ = await turn_on(ws, live, {"prompt_id": pid, "version_id": v1["id"]})
        check(status == "ok" and system_prompt_sent().startswith(V1), "open session pins v1")
        s, body = api("DELETE", f"/api/prompts/pins/{live}", MEMBER)
        check(s == 200 and body == {"cleared": True}, "pin cleared while the session stays open")
        status, _ = await turn_on(ws, live, ...)
        check(status == "ok" and not system_prompt_sent().startswith(V1),
              "the open session's next turn runs unpinned")
    s, stored = api("GET", f"/api/conversations/{live}", MEMBER)
    meta = stored.get("metadata") or {}
    check(s == 200 and "prompt_library" not in meta and meta.get("prompt_library_pin_revision") == 2,
          "the open session's save did not restore the cleared pin (set = 1, clear = 2)")

    # --- Two windows changing one conversation's pin -----------------------
    race = str(uuid.uuid4())
    async with websockets.connect(WS, additional_headers={"X-User-Email": MEMBER}) as tab_a, \
            websockets.connect(WS, additional_headers={"X-User-Email": MEMBER}) as tab_b:
        status, saved = await turn_on(tab_a, race, {"prompt_id": pid, "version_id": v1["id"]})
        seen = saved.get("prompt_library_pin_revision") if saved else None
        check(status == "ok" and seen == 1, "conversation_saved reports the pin revision (1)")
        status, b_saved = await turn_on(tab_b, race, {"prompt_id": pid, "version_id": v2["id"]},
                                        {"library_prompt_revision": seen})
        check(status == "ok" and system_prompt_sent().startswith(V2), "tab B re-pins to v2 from revision 1")
        check(b_saved and (b_saved.get("prompt_library") or {}).get("version_id") == v2["id"]
              and b_saved.get("prompt_library_pin_revision") == 2,
              "tab B's conversation_saved reports the stored pin (v2, revision 2)")
        before = last_llm_request().get("timestamp")
        status, frame = await turn_on(tab_a, race, {"prompt_id": pid, "version_id": v1["id"]},
                                      {"library_prompt_revision": seen})
        check(status == "error" and frame.get("error_type") == "prompt_pin_conflict"
              and (frame.get("prompt_library") or {}).get("version_id") == v2["id"]
              and frame.get("prompt_library_pin_revision") == 2
              and last_llm_request().get("timestamp") == before,
              "tab A's stale change is refused with the stored pin, and nothing ran")
        check(frame.get("conversation_id") == race,
              "the conflict frame names the conversation it belongs to")
        status, frame = await turn_on(tab_a, race, None, {"library_prompt_revision": seen})
        check(status == "error" and frame.get("error_type") == "prompt_pin_conflict",
              "tab A's stale explicit clear (library_prompt: null) is refused too")
        status, _ = await turn_on(tab_a, race, None, {"library_prompt_revision": 2})
        check(status == "ok" and not system_prompt_sent().startswith((V1, V2)),
              "after catching up, tab A's explicit clear applies")
    s, stored = api("GET", f"/api/conversations/{race}", MEMBER)
    meta = stored.get("metadata") or {}
    check("prompt_library" not in meta and meta.get("prompt_library_pin_revision") == 3,
          "the stored pin went v1 -> v2 -> cleared, one revision each")

    # --- Save-and-publish publishes only what was saved -------------------
    rev = draft_revision(pid)
    s, a_saved = api("PUT", f"/api/prompts/{pid}", OWNER, {"content": V2 + " (A)", "expected_draft_revision": rev})
    a_saved = a_saved["prompt"]
    check(s == 200 and a_saved["draft_content"] == V2 + " (A)" and a_saved["draft_revision"] == rev + 1,
          "a save returns exactly the draft it wrote")
    api("PUT", f"/api/prompts/{pid}", OWNER, {"content": "B'S UNSEEN TEXT", "expected_draft_revision": rev + 1})
    s, _ = api("POST", f"/api/prompts/{pid}/publish", OWNER, {"expected_draft_revision": a_saved["draft_revision"]})
    s_hist, history = api("GET", f"/api/prompts/{pid}/versions?limit=1", OWNER)
    check(s_hist == 200 and history["total"] == 2 and len(history["versions"]) == 1
          and "content" not in history["versions"][0],
          "version history is paged and carries no prompt text")
    versions = api("GET", f"/api/prompts/{pid}/versions", OWNER)[1]["versions"]
    texts = [api("GET", f"/api/prompts/{pid}/versions/{v['id']}", OWNER)[1]["version"]["content"] for v in versions]
    check(s == 409 and all("B'S UNSEEN" not in t for t in texts),
          "publishing the saved revision after another save is refused")

    # --- Revocation --------------------------------------------------------
    share(pid, [])
    status, frame = await chat(MEMBER, conv, ...)
    check(status == "error" and frame.get("error_type") == "authorization",
          "after unsharing, continuing the pinned conversation is refused")
    s, stored = api("GET", f"/api/conversations/{conv}", MEMBER)
    check(s == 200 and stored["metadata"]["prompt_library"]["version_id"] == v1["id"],
          "history keeps its pinned reference after revocation")
    status, frame = await chat(MEMBER, conv, ...)
    check(status == "error" and f"/api/prompts/pins/{conv}" in frame.get("message", ""),
          "a client that cannot pick a prompt is told how to clear the pin")
    s, _ = api("DELETE", f"/api/prompts/pins/{conv}", OUTSIDER)
    check(s == 404, "only the conversation owner can clear its pin")
    s, body = api("DELETE", f"/api/prompts/pins/{conv}", MEMBER)
    check(s == 200 and body == {"cleared": True}, "the owner clears the unavailable pin")
    status, _ = await chat(MEMBER, conv, ...)
    check(status == "ok" and not system_prompt_sent().startswith(V1),
          "after clearing, a legacy-style turn continues with the default prompt")
    s, stored = api("GET", f"/api/conversations/{conv}", MEMBER)
    history = (stored.get("metadata") or {}).get("prompt_library_history") or []
    check(any(h.get("version_id") == v1["id"] for h in history), "the pin history still records v1")

    # --- Fork and audit ----------------------------------------------------
    share(pid, ["eng"])
    s, body = api("POST", f"/api/prompts/{pid}/fork", MEMBER, {"version_id": v1["id"]})
    check(s == 200 and body["prompt"]["is_owner"] and body["prompt"]["draft_content"] == V1,
          "member forks v1 into a private copy")
    s, _ = api("GET", f"/api/prompts/{body['prompt']['id']}", OWNER)
    check(s == 404, "the fork is private to the member")
    s, body = api("GET", f"/api/prompts/{pid}/audit", OWNER)
    kinds = {e["event_type"] for e in body["events"]}
    check({"create", "publish", "share", "update_draft", "use", "use_denied", "unpin", "fork"} <= kinds,
          "audit trail records create/publish/share/update/use/denied/unpin/fork")
    check("SYSTEM-PROMPT" not in json.dumps(body), "audit trail holds no prompt text")
    s, _ = api("GET", f"/api/prompts/{pid}/audit", MEMBER)
    check(s == 403, "audit trail needs manage permission")

    print(f"\nlibrary_e2e: {passed} passed, {failed} failed")
    return failed == 0


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
