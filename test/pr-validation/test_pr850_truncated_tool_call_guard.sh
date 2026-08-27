#!/bin/bash
# Validation script for PR #850: a truncated tool call must not poison the
# conversation.
#
# Observed in production: a model ran out of output tokens partway through a
# tool call, so `arguments` arrived as an unparseable fragment. Atlas appended
# it verbatim to the assistant message, and because providers re-parse every
# tool call in the history on each request, every later turn failed with
# "OpenAIException - Unterminated string starting at: line 1 column 73" -- a 400
# no retry could clear, because the poison was in the history, not the request.
#
# Test plan (end-to-end through the real streaming/chat paths, not import checks):
# - A streamed truncated tool call never reaches the caller, and the fragment
#   never reaches the message history.
# - The turn fails with LLMMalformedToolCallError, whose message tells the user
#   the failure is retryable, and names the token limit only when
#   finish_reason == "length".
# - Well-formed sibling calls in the same response still run.
# - The non-streaming path guards identically.
# - Narration streamed before the failure does not hide it: the agentic loop
#   re-raises, and tools mode sends an error frame with the new error_type.
# - The RAG+tools wrapper re-raises rather than falling back and losing context.
# - Unit tests: atlas/tests/test_malformed_tool_call_guard.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# The exact fragment shape from the production log: the model stopped mid-string
# inside the arguments object, 72 characters in.
read -r -d '' HARNESS <<'PYHARNESS'
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TRUNCATED = '{"filename": "1787784579_62f6178a_MC5094_4A1200_3.0.0_0_topic'

def caller():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller
    cfg = MagicMock()
    cfg.models = {}
    c = LiteLLMCaller(llm_config=cfg)
    c._get_litellm_model_name = MagicMock(return_value="openai/gemma")
    c._get_model_kwargs = MagicMock(return_value={"max_tokens": 100})
    c._prepare_messages = MagicMock(side_effect=lambda m, msgs: msgs)
    return c

def chunk(index=0, tool_id=None, name=None, args=None, finish_reason=None, text=None):
    tool_calls = None
    if tool_id is not None or name is not None or args is not None:
        fn = SimpleNamespace(name=name, arguments=args)
        tool_calls = [SimpleNamespace(index=index, id=tool_id, function=fn)]
    delta = SimpleNamespace(content=text, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])

async def drain(c, chunks):
    async def fake(*a, **k):
        async def gen():
            for ch in chunks:
                yield ch
        return gen()
    out, final = [], None
    with patch("atlas.modules.llm.litellm_streaming.acompletion", fake):
        async for item in c.stream_with_tools(
            "gemma", [{"role": "user", "content": "hi"}], [{"type": "function"}],
        ):
            (out.append(item) if isinstance(item, str) else None)
            if not isinstance(item, str):
                final = item
    return out, final
PYHARNESS

# ==========================================
# Check 1: the truncated fragment never leaves the LLM layer
# ==========================================
print_header "Check 1: a truncated tool call fails the turn instead of being returned"

python3 -c "
import asyncio
$HARNESS
from atlas.domain.errors import LLMMalformedToolCallError

async def main():
    c = caller()
    try:
        await drain(c, [
            chunk(tool_id='call-1', name='read_file', args=TRUNCATED),
            chunk(finish_reason='length'),
        ])
    except LLMMalformedToolCallError as exc:
        assert exc.tool_names == ['read_file'], exc.tool_names
        assert 'try again' in exc.message.lower(), exc.message
        assert 'cut off' in exc.message.lower(), exc.message
        assert TRUNCATED not in exc.message, 'fragment must not leak into the user message'
        print('raised:', exc.message)
        return
    raise AssertionError('a truncated tool call was returned to the caller')

asyncio.run(main())
"
print_result $? "truncated tool call raises LLMMalformedToolCallError with a retryable message"

# ==========================================
# Check 2: bad JSON without finish_reason=length does not blame the token limit
# ==========================================
print_header "Check 2: the message only blames the token limit with evidence"

python3 -c "
import asyncio
$HARNESS
from atlas.domain.errors import LLMMalformedToolCallError

async def main():
    c = caller()
    try:
        await drain(c, [
            chunk(tool_id='call-1', name='read_file', args='not json at all'),
            chunk(finish_reason='tool_calls'),
        ])
    except LLMMalformedToolCallError as exc:
        assert 'cut off' not in exc.message.lower(), exc.message
        assert 'not valid json' in exc.message.lower(), exc.message
        print('raised:', exc.message)
        return
    raise AssertionError('malformed JSON was accepted')

asyncio.run(main())
"
print_result $? "malformed (not truncated) arguments get their own message"

# ==========================================
# Check 3: well-formed sibling calls survive
# ==========================================
print_header "Check 3: one bad call does not discard the calls that arrived intact"

python3 -c "
import asyncio
$HARNESS

async def main():
    c = caller()
    _, final = await drain(c, [
        chunk(index=0, tool_id='ok', name='search', args='{\"q\": \"hi\"}'),
        chunk(index=1, tool_id='bad', name='read_file', args=TRUNCATED),
        chunk(finish_reason='length'),
    ])
    ids = [tc.id for tc in final.tool_calls]
    assert ids == ['ok'], ids
    args = [tc.function.arguments for tc in final.tool_calls]
    assert all('topic' not in a for a in args), args
    print('kept:', ids)

asyncio.run(main())
"
print_result $? "well-formed calls still run when a sibling is truncated"

# ==========================================
# Check 4: the fragment never reaches the assistant message
# ==========================================
print_header "Check 4: the fragment never enters the conversation history"

python3 -c "
import asyncio
$HARNESS
from atlas.application.chat.agent.agentic_loop import _to_tool_call_dict

async def main():
    c = caller()
    _, final = await drain(c, [
        chunk(index=0, tool_id='ok', name='search', args='{\"q\": \"hi\"}'),
        chunk(index=1, tool_id='bad', name='read_file', args=TRUNCATED),
        chunk(finish_reason='length'),
    ])
    # This is exactly how the agentic loop builds the assistant message that is
    # re-sent to the provider on every later turn.
    serialized = [_to_tool_call_dict(tc) for tc in final.tool_calls]
    import json
    for tc in serialized:
        json.loads(tc['function']['arguments'])  # must parse, or history is poisoned
    assert TRUNCATED not in json.dumps(serialized)
    print('history tool_calls all parse:', serialized)

asyncio.run(main())
"
print_result $? "every tool call written to history has parseable arguments"

# ==========================================
# Check 5: the non-streaming path guards identically
# ==========================================
print_header "Check 5: call_with_tools applies the same guard"

python3 -c "
import asyncio
from unittest.mock import AsyncMock
$HARNESS
from atlas.domain.errors import LLMMalformedToolCallError

async def main():
    c = caller()
    message = SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
        id='call-1', type='function',
        function=SimpleNamespace(name='read_file', arguments=TRUNCATED))])
    c._acompletion_with_retry = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason='length')]))
    try:
        await c.call_with_tools('gemma', [{'role': 'user', 'content': 'hi'}], [{'type': 'function'}])
    except LLMMalformedToolCallError as exc:
        print('raised:', exc.message)
        return
    raise AssertionError('non-streaming path returned a truncated tool call')

asyncio.run(main())
"
print_result $? "non-streaming call_with_tools rejects a truncated tool call"

# ==========================================
# Check 6: narration streamed first does not hide the failure
# ==========================================
print_header "Check 6: partial narration does not turn a skipped call into an answer"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from atlas.domain.errors import LLMMalformedToolCallError

class StreamThenFail:
    async def stream_with_tools(self, model, messages, tools_schema, tool_choice='auto',
                                temperature=0.7, user_email=None):
        yield 'Let me read that file for you.'
        raise LLMMalformedToolCallError('The model ran out of room.', tool_names=['read_file'])

    async def stream_with_rag_and_tools(self, model, messages, data_sources, tools_schema,
                                        user_email, tool_choice='auto', temperature=0.7):
        async for item in self.stream_with_tools(model, messages, tools_schema, tool_choice):
            yield item

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        yield 'unused'

async def main():
    # Agent mode: the loop must re-raise rather than report the narration as done.
    from atlas.application.chat.agent.agentic_loop import AgenticLoop
    loop = AgenticLoop(llm=StreamThenFail(), tool_manager=None, prompt_provider=None)
    pub = AsyncMock()
    ctx = MagicMock()
    ctx.user_email = 'u@example.com'
    try:
        await loop._call_llm_streaming('gemma', [{'role': 'user', 'content': 'read it'}],
                                       [{'type': 'function'}], None, ctx, 0.7, pub)
        raise AssertionError('agentic loop swallowed the malformed tool call')
    except LLMMalformedToolCallError:
        pass
    assert pub.publish_token_stream.await_args.kwargs['is_last'] is True, 'stream left open'

    # Tools mode: the runner must send an error frame carrying the new type.
    from atlas.application.chat.modes.tools import ToolsModeRunner
    pub2 = AsyncMock()
    tm = MagicMock()
    tm.get_tools_schema = MagicMock(return_value=[{'type': 'function'}])
    runner = ToolsModeRunner(llm=StreamThenFail(), tool_manager=tm,
                             event_publisher=pub2, config_manager=None)
    session = MagicMock()
    session.history = MagicMock()
    session.session_id = 's1'
    session.files = {}
    with patch('atlas.application.chat.modes.tools.tool_executor') as te:
        te.build_files_manifest = MagicMock(return_value=None)
        await runner.run_streaming(session=session, model='gemma',
                                   messages=[{'role': 'user', 'content': 'read it'}],
                                   selected_tools=['read_file'])
    errors = [c.args[0] for c in pub2.send_json.await_args_list
              if c.args and c.args[0].get('type') == 'error']
    assert errors, 'tools mode hid the dropped tool call behind the narration'
    assert errors[0]['error_type'] == 'malformed_tool_call', errors[0]
    print('tools mode error frame:', errors[0]['error_type'])

asyncio.run(main())
"
print_result $? "both streaming consumers surface the failure despite partial text"

# ==========================================
# Check 7: the RAG+tools wrapper re-raises instead of retrying without context
# ==========================================
print_header "Check 7: RAG+tools wrapper does not swallow the error into a fallback"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
$HARNESS
from atlas.domain.errors import LLMMalformedToolCallError
from atlas.modules.rag.client import RAGResponse

async def main():
    c = caller()
    c._rag_service = MagicMock()
    # One source answered; the RAG context is already built into the messages by
    # the time the tool call fails.
    c._query_all_rag_sources = AsyncMock(
        return_value=([('corpus', RAGResponse(content='retrieved context'))], [], []))

    calls = []
    async def failing_call_with_tools(*a, **k):
        calls.append(a[1])
        raise LLMMalformedToolCallError('The model ran out of room.', tool_names=['read_file'])
    c.call_with_tools = failing_call_with_tools

    try:
        await c.call_with_rag_and_tools(
            'gemma', [{'role': 'user', 'content': 'hi'}], ['corpus'],
            [{'type': 'function'}], 'u@example.com',
        )
    except LLMMalformedToolCallError as exc:
        # Exactly one attempt: a second would be the fallback retry, which
        # re-sends the original messages and throws away the RAG context.
        assert len(calls) == 1, f'fallback retry fired: {len(calls)} calls'
        assert any('retrieved context' in str(m) for m in calls[0]), 'RAG context missing'
        print('propagated after', len(calls), 'attempt; RAG context preserved')
        return
    raise AssertionError('the error did not reach the caller')

asyncio.run(main())
"
print_result $? "LLMMalformedToolCallError propagates through call_with_rag_and_tools"

# ==========================================
# Check 8: error classification reaches the client as its own type
# ==========================================
print_header "Check 8: the error is classified, not generalized"

python3 -c "
from atlas.application.chat.utilities.error_handler import classify_llm_error, error_type_for
from atlas.domain.errors import LLMMalformedToolCallError
from atlas.modules.llm.litellm_caller import LiteLLMCaller

err = LLMMalformedToolCallError('The model ran out of room.', tool_names=['read_file'])
cls, user_msg, log_msg = classify_llm_error(err)
assert cls is LLMMalformedToolCallError, cls
assert user_msg == 'The model ran out of room.', user_msg
assert error_type_for(LLMMalformedToolCallError) == 'malformed_tool_call'

# The guard raises from inside the block that wraps the provider call, so an
# already-typed domain error must pass through _raise_llm_domain_error untouched.
try:
    LiteLLMCaller._raise_llm_domain_error(err)
except LLMMalformedToolCallError:
    pass
else:
    raise AssertionError('domain error was reclassified')
print('classified as:', error_type_for(cls))
"
print_result $? "classify_llm_error and _raise_llm_domain_error preserve the error"

# ==========================================
# Check 9: unit tests
# ==========================================
print_header "Check 9: unit tests"

cd "$ATLAS_DIR" || exit 1
python3 -m pytest tests/test_malformed_tool_call_guard.py \
    tests/test_tool_error_attribution.py \
    tests/test_streaming_token_flow.py \
    tests/test_agentic_loop.py \
    tests/test_tools_mode_iteration.py \
    -q > /tmp/pr850_pytest_$$.log 2>&1
print_result $? "affected unit test files pass"
tail -5 /tmp/pr850_pytest_$$.log

print_header "Summary"
echo "Passed:  $PASSED"
echo "Failed:  $FAILED"

[ "$FAILED" -eq 0 ]
