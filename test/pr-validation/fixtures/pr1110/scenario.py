"""End-to-end scenario for context compaction (issue #877) against a live backend.

1. Plain turns grow a conversation past the threshold: automatic compaction
   writes a conversation checkpoint before the turn, the next request carries
   the checkpoint instead of the early turns, and the stored transcript keeps
   every original message plus the checkpoint row.
2. Manual compaction (`compact_context`, with a focus) on another
   conversation, adding no user turn; the auto toggle off means no automatic
   compaction even past the threshold.
3. A tool-heavy agent run crosses the threshold mid-run and keeps going on its
   own, and a context-length error mid-run is recovered by compacting and
   retrying; no tool call is executed twice.
4. The context meter's estimate for an unknown alias reports an assumed window.
"""
import asyncio
import json
import os
import sys
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
MOCK = os.environ["MOCK_LLM_PORT"]
USER = "owner@example.com"
MODEL = "compaction-mock"
FAILED = 0


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def mock_log():
    with urllib.request.urlopen(f"http://127.0.0.1:{MOCK}/test/log") as r:
        return json.loads(r.read())


def stored(conversation_id):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/conversations/{conversation_id}",
        headers={"X-User-Email": USER},
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


async def connect():
    return await websockets.connect(
        f"ws://127.0.0.1:{PORT}/ws", additional_headers={"X-User-Email": USER}, max_size=None)


async def turn(ws, frame, frames, timeout=60):
    """Send a frame and collect everything until the turn completes."""
    await ws.send(json.dumps(frame))
    saved = None
    usage_seen = False
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        req = m.get("frame") if m.get("type") == "background_activity" else m
        if req and req.get("type") == "tool_approval_request":
            await ws.send(json.dumps({
                "type": "tool_approval_response", "tool_call_id": req["tool_call_id"],
                "approved": True, "arguments": req.get("arguments"),
                "run_id": req.get("run_id"), "conversation_id": req.get("conversation_id"),
            }))
        frames.append(m)
        if m.get("type") == "conversation_saved":
            saved = m.get("conversation_id")
        if m.get("type") == "error":
            return saved
        if m.get("type") == "context_usage":
            usage_seen = True
        # The usage report and the save can arrive in either order.
        if usage_seen and saved:
            return saved


def chat(content, conversation_id=None, auto=True, **extra):
    frame = {"type": "chat", "content": content, "model": MODEL, "save_mode": "server",
             "auto_compact": auto, **extra}
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return frame


def long_text(tag, words=450):
    return f"{tag} " + " ".join(f"{tag.lower()}w{i}" for i in range(words))


def of(frames, kind, **match):
    return [f for f in frames if f.get("type") == kind and all(f.get(k) == v for k, v in match.items())]


async def auto_between_turns():
    ws = await connect()
    frames = []
    conv = None
    for i in range(6):
        conv = await turn(ws, chat(long_text(f"TURN-{i}"), conv), frames) or conv
        if of(frames, "context_compaction", status="completed"):
            break
    done = of(frames, "context_compaction", status="completed")
    check(bool(done), "automatic compaction ran before a turn once the conversation neared the limit")
    if not done:
        await ws.close()
        return
    check(done[0]["trigger"] == "auto" and done[0]["scope"] == "conversation", "it wrote a conversation checkpoint")
    check(done[0]["after_tokens"] < done[0]["before_tokens"], "the checkpoint made the next request smaller")
    usage = of(frames, "context_usage")[-1]
    check(usage["window_source"] == "config" and usage["context_window"] == 6000, "usage reports the configured window")
    check(usage["checkpoints"] >= 1, "usage counts the checkpoint")
    check(isinstance(usage.get("provider_last_prompt_tokens"), int), "provider-reported prompt size reaches the meter")

    turn_requests = [e for e in mock_log() if not e["summarizer"]]
    last = turn_requests[-1]
    check(last["has_checkpoint"] and "TURN-0" not in last["contains"], "the compacted request carries the checkpoint, not the first turn")

    record = stored(conv)
    user_rows = [m for m in record["messages"] if m["role"] == "user"]
    check(any("TURN-0" in m["content"] for m in user_rows), "the stored transcript still has the first turn")
    checkpoints = [m for m in record["messages"] if m.get("message_type") == "context_checkpoint"]
    check(len(checkpoints) >= 1 and checkpoints[0]["metadata"]["context_checkpoint"]["summary"].startswith("## Goal"),
          "the checkpoint row is stored with its summary")
    await ws.close()

    # A new connection rehydrates the conversation from the store (fresh
    # message ids): the checkpoint must still replace the early turns.
    ws = await connect()
    await turn(ws, chat("TURN-5 short follow-up", conv, auto=False), [])
    last = [e for e in mock_log() if not e["summarizer"]][-1]
    check(last["has_checkpoint"] and "TURN-0" not in last["contains"] and "TURN-5" in last["contains"],
          "after a reload the stored checkpoint still applies")
    await ws.close()


async def manual_and_toggle():
    ws = await connect()
    frames = []
    conv = None
    for i in range(3):
        conv = await turn(ws, chat(long_text(f"M-{i}", 120), conv, auto=False), frames) or conv
    check(not of(frames, "context_compaction"), "nothing compacts on its own with the toggle off")
    before = len(stored(conv)["messages"])
    await turn(ws, {"type": "compact_context", "model": MODEL, "conversation_id": conv,
                    "focus": "the M decisions", "save_mode": "server"}, frames)
    done = of(frames, "context_compaction", status="completed")
    check(bool(done) and done[0]["trigger"] == "manual", "manual compaction completed")
    record = stored(conv)
    check(len(record["messages"]) == before + 1, "manual compaction added only the checkpoint row")
    check(sum(1 for m in record["messages"] if m["role"] == "user") == 3, "no user turn was added")
    focus = [m for m in record["messages"] if m.get("message_type") == "context_checkpoint"][0]
    check(focus["metadata"]["context_checkpoint"]["focus"] == "the M decisions", "the focus is recorded")

    # Past the threshold with the toggle off: no compaction, the meter says so.
    frames.clear()
    for i in range(3, 7):
        await turn(ws, chat(long_text(f"M-{i}"), conv, auto=False), frames)
    usage = of(frames, "context_usage")[-1]
    check(not of(frames, "context_compaction") and usage["auto_compact"] is False, "the off toggle is honoured past the threshold")
    await ws.close()


async def agent_run():
    ws = await connect()
    frames = []
    content = "label=AGENT steps=8 narrate=260 overflow_at=6 please run the audit"
    await turn(ws, chat(content, agent_mode=True, agent_max_steps=12,
                        selected_tools=["atlas_sleep"]), frames, timeout=120)
    if os.environ.get("SCENARIO_DEBUG"):
        for f in frames:
            if f.get("type") != "token_stream":
                print("  frame:", json.dumps({k: (v if not isinstance(v, str) else v[:120]) for k, v in f.items() if k not in ("summary",)})[:300])
    completed = of(frames, "context_compaction", status="completed", scope="run")
    triggers = [f["trigger"] for f in completed]
    check("auto" in triggers, "the run compacted itself mid-run")
    check("overflow" in triggers, "a context-length error was recovered by compacting and retrying")
    tokens = "".join(f.get("token", "") for f in frames if f.get("type") == "token_stream")
    check("FINAL[AGENT] done after 8 steps" in tokens, "the run finished its task without a new user message")
    starts = [f for f in frames if f.get("type") == "tool_start"]
    check(len(starts) == 8, f"every tool call ran exactly once ({len(starts)} executions for 8 calls)")
    check(not of(frames, "error"), "no error reached the user")
    await ws.close()


async def unknown_alias():
    ws = await connect()
    frames = []
    await turn(ws, chat("hello there", model="unknown-alias"), frames)
    usage = of(frames, "context_usage")[-1]
    check(usage["window_source"] == "fallback" and usage["context_window"] == 128000,
          "an alias LiteLLM does not know reports an assumed window")
    await ws.close()


async def main():
    await auto_between_turns()
    await manual_and_toggle()
    await agent_run()
    await unknown_alias()
    print(f"\n{FAILED} check(s) failed", flush=True)
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    asyncio.run(main())
