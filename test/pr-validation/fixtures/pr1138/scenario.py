"""End-to-end scenario for empty checkpoint recovery (issue #1138) against a live backend.

The mock model is scripted to answer compaction summarizer requests with no
content -- the failure the bug report describes. Four passes over a real
backend on the WebSocket:

1. A summarizer that never returns text: the compaction fails with a classified
   `reason` (`empty_content`) after exactly one bounded retry that asks for a
   larger output budget; the turn still completes on its original context and
   no checkpoint row is stored.
2. A summary whose model spent its whole output budget on reasoning: the reason
   is `output_token_exhaustion`, and the log carries the finish reason and the
   reasoning-token count.
3. A transient blank reply: the retry recovers, the checkpoint is stored, and
   later requests carry it instead of the earlier turns.
4. A model configured with `reasoning_effort` already asks for the larger,
   reasoning-aware output budget on the first attempt.
5. The operator log carries the diagnostics and none of the transcript content.

Nothing on the path is stubbed.
"""
import asyncio
import json
import os
import subprocess
import sys
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
MOCK = os.environ["MOCK_LLM_PORT"]
BACKEND_LOG = os.environ.get("BACKEND_LOG", "")
USER = "owner@example.com"
MODEL = "compaction-mock"
REASONING_MODEL = "reasoning-mock"
# A marker planted in the transcript: it must never reach the log.
CONTENT_MARKER = "SECRET-PASSWORD-1234"
FAILED = 0


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def _post(path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{MOCK}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


def set_mode(mode):
    _post("/test/mode", {"mode": mode})


def mock_log():
    with urllib.request.urlopen(f"http://127.0.0.1:{MOCK}/test/log") as response:
        return json.loads(response.read())


def stored(conversation_id):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/conversations/{conversation_id}",
        headers={"X-User-Email": USER},
    )
    with urllib.request.urlopen(req) as response:
        return json.loads(response.read())


async def connect():
    return await websockets.connect(
        f"ws://127.0.0.1:{PORT}/ws", additional_headers={"X-User-Email": USER}, max_size=None)


async def turn(ws, frame, frames, timeout=90):
    """Send a frame and collect everything until the turn completes."""
    await ws.send(json.dumps(frame))
    saved = None
    usage_seen = False
    while True:
        message = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        frames.append(message)
        if message.get("type") == "conversation_saved":
            saved = message.get("conversation_id")
        if message.get("type") == "error":
            return saved
        if message.get("type") == "context_usage":
            usage_seen = True
        # The usage report and the save can arrive in either order.
        if usage_seen and saved:
            return saved


def chat(content, conversation_id=None, auto=True, model=None, **extra):
    frame = {"type": "chat", "content": content, "model": model or MODEL, "save_mode": "server",
             "auto_compact": auto, **extra}
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return frame


def long_text(tag, words=450):
    return f"{tag} " + " ".join(f"{tag.lower()}w{i}" for i in range(words))


def of(frames, kind, **match):
    return [f for f in frames if f.get("type") == kind and all(f.get(k) == v for k, v in match.items())]


def summarizer_calls():
    return [entry for entry in mock_log() if entry["summarizer"]]


def constant(name):
    """Read a module constant, so the script asserts the shipped value."""
    code = f"from atlas.application.chat.context.compaction_engine import {name} as value; print(value)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    return int(result.stdout.strip())


async def grow_until(ws, frames, tag, status, turns=6, model=None):
    """Send long turns until a compaction frame with ``status`` appears."""
    conversation = None
    sent = 0
    for i in range(turns):

        sent += 1
        # A marker rides along in every turn: it must never reach the log.
        content = f"{tag}{i} " + long_text(f"{tag}{i}") + f" {CONTENT_MARKER}"
        conversation = await turn(
            ws, chat(content, conversation, model=model), frames,
        ) or conversation
        if of(frames, "context_compaction", status=status):
            break
    return conversation, sent


async def never_returns_text():
    """A summarizer that never replies with text: fail, once, with a reason."""
    set_mode("always_empty")
    ws = await connect()
    frames = []
    conversation, sent = await grow_until(ws, frames, "T", "failed")
    failed = of(frames, "context_compaction", status="failed")
    check(bool(failed), "a summarizer that never returns text fails the compaction")
    if not failed:
        await ws.close()
        return
    check(failed[0].get("reason") == "empty_content",
          f"the failure is classified as a blank reply (reason={failed[0].get('reason')})")
    check("no checkpoint text" in failed[0]["error"], "the user-facing reason names the cause")

    calls = summarizer_calls()
    check(len(calls) == 2 and all(call["empty"] for call in calls),
          f"the summarizer was called once, then retried once ({len(calls)} calls)")
    check(calls[1]["max_tokens"] > calls[0]["max_tokens"],
          "the retry asks for a larger output budget than the first attempt")

    # The turn ran on its original context, and nothing was dropped.
    tokens = "".join(f.get("token", "") for f in frames if f.get("type") == "token_stream")
    check("ECHO:" in tokens, "the turn completed with its uncompacted context")
    rows = stored(conversation)["messages"]
    check(sum(1 for row in rows if row["role"] == "user") == sent,
          "the stored transcript keeps every user turn")
    check(not [row for row in rows if row.get("message_type") == "context_checkpoint"],
          "no checkpoint row was written for the failed compaction")
    await ws.close()


async def exhausted_budget_is_named():
    """A budget drained by reasoning is reported as its own cause."""
    set_mode("exhausted")
    ws = await connect()
    frames = []
    conversation, _ = await grow_until(ws, frames, "E", "failed")
    failed = of(frames, "context_compaction", status="failed")
    check(bool(failed), "a reasoning model that drains the budget fails the compaction")
    if not failed:
        await ws.close()
        return
    check(failed[0].get("reason") == "output_token_exhaustion",
          f"the failure is classified as an exhausted budget (reason={failed[0].get('reason')})")
    check("output-token budget" in failed[0]["error"], "the user copy names the cause")
    calls = summarizer_calls()
    check(len(calls) == 2 and calls[0]["finish_reason"] == "length",
          "the blank attempts were retried once and reported the finish reason")
    await ws.close()


async def transient_blank_reply():
    """A one-off blank reply recovers on the bounded retry."""
    set_mode("empty_once")
    ws = await connect()
    frames = []
    conversation, _ = await grow_until(ws, frames, "A", "completed")
    done = of(frames, "context_compaction", status="completed")
    check(bool(done), "a transient blank reply is recovered by the bounded retry")
    if not done:
        await ws.close()
        return
    check(done[0]["summary"].startswith("## Goal"), "the recovered summary is the checkpoint")
    check(done[0]["after_tokens"] < done[0]["before_tokens"], "the checkpoint shrank the request")

    blank = [entry for entry in summarizer_calls() if entry["empty"]]
    filled = [entry for entry in summarizer_calls() if not entry["empty"]]
    check(len(blank) == 1 and bool(filled), "the first attempt was blank and the retry answered")
    check(filled[0]["max_tokens"] > blank[0]["max_tokens"],
          "the retry that recovered used the larger budget")
    # The checkpoint, not the early turns, is what the model sees afterwards.
    check(any(entry["has_checkpoint"] for entry in mock_log() if not entry["summarizer"]),
          "later requests carry the checkpoint")
    record = stored(conversation)["messages"]
    check(len([row for row in record if row.get("message_type") == "context_checkpoint"]) == 1,
          "one checkpoint row was stored")
    check(any("A0" in row["content"] for row in record if row["role"] == "user"),
          "the stored transcript still has the first turn")
    await ws.close()


async def reasoning_model_gets_the_larger_budget():
    """The reasoning-aware budget is asked for from the first attempt."""
    set_mode("empty_once")
    ws = await connect()
    frames = []
    _, sent = await grow_until(ws, frames, "R", "completed", turns=24, model=REASONING_MODEL)
    done = of(frames, "context_compaction", status="completed")
    check(bool(done) and sent > 0,
          "compaction completes for a model configured with a reasoning effort")
    if not done:
        await ws.close()
        return
    first = [entry for entry in summarizer_calls()
             if entry["model"] == REASONING_MODEL and entry["attempt"] == 0]
    check(bool(first) and first[0]["max_tokens"] == constant("SUMMARY_REASONING_MAX_TOKENS"),
          "the first summarizer attempt uses the reasoning-aware budget")
    check(constant("SUMMARY_REASONING_MAX_TOKENS") > constant("SUMMARY_MAX_TOKENS"),
          "the reasoning-aware budget is larger than the plain one")
    await ws.close()


def log_checks():
    """The failure is diagnosable from the operator log alone (#1138)."""
    if not BACKEND_LOG or not os.path.exists(BACKEND_LOG):
        check(False, f"backend log is available at {BACKEND_LOG}")
        return
    with open(BACKEND_LOG, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    lines = [line for line in text.splitlines()
             if "Empty checkpoint" in line or "returned no content" in line]
    check(bool(lines), "the empty checkpoint is logged with diagnostics")
    check(any("output_token_exhaustion" in line for line in lines),
          "the log names the exhausted-budget reason")
    check(any("'finish_reason': 'length'" in line or '"finish_reason": "length"' in line
              for line in lines),
          "the log carries the provider finish reason")
    check(any("reasoning_tokens" in line for line in lines if "Empty checkpoint" in line),
          "the log reports reasoning-token usage")
    check(CONTENT_MARKER not in text, "the log carries none of the transcript content")
    check(all("http://" not in line for line in lines),
          "the log lines carry no provider URL")


async def main():
    await never_returns_text()
    await exhausted_budget_is_named()
    await transient_blank_reply()
    await reasoning_model_gets_the_larger_budget()
    log_checks()
    print(f"\nScenario failures: {FAILED}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
