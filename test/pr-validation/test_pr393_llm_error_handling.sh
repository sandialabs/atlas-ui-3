#!/usr/bin/env bash
# PR #393 - LLM errors cause chat to hang indefinitely instead of returning error to user
# Validates that LLM errors are properly classified and propagated to the frontend.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
PASSED=0
FAILED=0

pass() { echo "PASSED: $1"; PASSED=$((PASSED + 1)); }
fail() { echo "FAILED: $1"; FAILED=$((FAILED + 1)); }

# Activate venv
source "$PROJECT_ROOT/.venv/bin/activate"
export PYTHONPATH="$PROJECT_ROOT"

echo "============================================"
echo "PR #393 - LLM Error Handling Validation"
echo "============================================"

# -------------------------------------------------------------------
# 1. Behavioral: _raise_llm_domain_error maps litellm errors correctly
# -------------------------------------------------------------------
echo ""
echo "--- Test 1: _raise_llm_domain_error maps litellm errors correctly ---"
if python -c "
import litellm
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.domain.errors import RateLimitError, LLMTimeoutError, LLMAuthenticationError, LLMServiceError

# Test rate limit
try:
    LiteLLMCaller._raise_llm_domain_error(litellm.RateLimitError('rate limit exceeded', 'model', None, None))
    assert False, 'Should have raised'
except RateLimitError as e:
    assert 'high traffic' in str(e).lower(), f'Bad message: {e}'
    print('  Rate limit -> RateLimitError: OK')

# Test timeout
try:
    LiteLLMCaller._raise_llm_domain_error(litellm.Timeout('request timed out', 'model', None))
    assert False, 'Should have raised'
except LLMTimeoutError as e:
    assert 'timed out' in str(e).lower(), f'Bad message: {e}'
    print('  Timeout -> LLMTimeoutError: OK')

# Test auth error
try:
    LiteLLMCaller._raise_llm_domain_error(litellm.AuthenticationError('invalid api key', 'model', None, None))
    assert False, 'Should have raised'
except LLMAuthenticationError as e:
    assert 'authentication' in str(e).lower(), f'Bad message: {e}'
    print('  AuthenticationError -> LLMAuthenticationError: OK')

# Test generic error
try:
    LiteLLMCaller._raise_llm_domain_error(Exception('something went wrong'))
    assert False, 'Should have raised'
except LLMServiceError as e:
    assert 'try again' in str(e).lower(), f'Bad message: {e}'
    print('  Generic Exception -> LLMServiceError: OK')

print('All error mappings correct')
"; then
    pass "LiteLLMCaller maps litellm exceptions to domain errors correctly"
else
    fail "LiteLLMCaller error mapping is incorrect"
fi

# -------------------------------------------------------------------
# 2. Behavioral: call_plain raises domain errors on LLM failure
#    Uses mock to patch acompletion and _get_litellm_model_name
# -------------------------------------------------------------------
echo ""
echo "--- Test 2: call_plain raises domain errors on LLM failure ---"
if python -c "
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
import litellm
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.domain.errors import RateLimitError, LLMServiceError

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller.llm_config = MagicMock()
caller.debug_mode = False
caller.rag_service = None

async def test_call_plain_rate_limit():
    with patch.object(caller, '_get_litellm_model_name', return_value='openai/test'), \
         patch.object(caller, '_get_model_kwargs', return_value={}), \
         patch.object(caller, '_sanitize_messages', return_value=[{'role': 'user', 'content': 'hi'}]), \
         patch('atlas.modules.llm.litellm_caller.acompletion',
               new_callable=AsyncMock,
               side_effect=litellm.RateLimitError('rate limit', 'model', None, None)):
        try:
            await caller.call_plain('test-model', [{'role': 'user', 'content': 'hi'}])
            assert False, 'Should have raised'
        except RateLimitError:
            print('  call_plain + RateLimitError: OK')
        except Exception as e:
            assert False, f'Wrong error type: {type(e).__name__}: {e}'

async def test_call_plain_generic():
    with patch.object(caller, '_get_litellm_model_name', return_value='openai/test'), \
         patch.object(caller, '_get_model_kwargs', return_value={}), \
         patch.object(caller, '_sanitize_messages', return_value=[{'role': 'user', 'content': 'hi'}]), \
         patch('atlas.modules.llm.litellm_caller.acompletion',
               new_callable=AsyncMock,
               side_effect=Exception('connection refused')):
        try:
            await caller.call_plain('test-model', [{'role': 'user', 'content': 'hi'}])
            assert False, 'Should have raised'
        except LLMServiceError:
            print('  call_plain + generic Exception -> LLMServiceError: OK')
        except Exception as e:
            assert False, f'Wrong error type: {type(e).__name__}: {e}'

asyncio.run(test_call_plain_rate_limit())
asyncio.run(test_call_plain_generic())
print('call_plain raises domain errors correctly')
"; then
    pass "call_plain raises domain errors on LLM failure"
else
    fail "call_plain does not raise domain errors"
fi

# -------------------------------------------------------------------
# 3. Behavioral: call_with_tools raises domain errors on LLM failure
# -------------------------------------------------------------------
echo ""
echo "--- Test 3: call_with_tools raises domain errors on LLM failure ---"
if python -c "
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
import litellm
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.domain.errors import LLMTimeoutError

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller.llm_config = MagicMock()
caller.debug_mode = False
caller.rag_service = None

async def test_call_with_tools_timeout():
    with patch.object(caller, '_get_litellm_model_name', return_value='openai/test'), \
         patch.object(caller, '_get_model_kwargs', return_value={}), \
         patch.object(caller, '_sanitize_messages', return_value=[{'role': 'user', 'content': 'hi'}]), \
         patch('atlas.modules.llm.litellm_caller.acompletion',
               new_callable=AsyncMock,
               side_effect=litellm.Timeout('timed out', 'model', None)):
        try:
            await caller.call_with_tools(
                'test-model',
                [{'role': 'user', 'content': 'hi'}],
                [{'type': 'function', 'function': {'name': 'test', 'parameters': {}}}],
            )
            assert False, 'Should have raised'
        except LLMTimeoutError:
            print('  call_with_tools + Timeout -> LLMTimeoutError: OK')
        except Exception as e:
            assert False, f'Wrong error type: {type(e).__name__}: {e}'

asyncio.run(test_call_with_tools_timeout())
print('call_with_tools raises domain errors correctly')
"; then
    pass "call_with_tools raises domain errors on LLM failure"
else
    fail "call_with_tools does not raise domain errors"
fi

# -------------------------------------------------------------------
# 4. Behavioral: AgentModeRunner sends agent_completion on error
# -------------------------------------------------------------------
echo ""
echo "--- Test 4: AgentModeRunner sends agent_completion on error ---"
if python -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from atlas.application.chat.modes.agent import AgentModeRunner
from atlas.application.chat.agent import AgentLoopFactory
from atlas.domain.errors import LLMServiceError
from atlas.domain.sessions.models import Session

# Track whether agent_completion was published
completion_events = []

async def mock_publish_agent_update(**kwargs):
    completion_events.append(kwargs)

# Create a mock agent loop that raises
mock_loop = MagicMock()
mock_loop.run = AsyncMock(side_effect=LLMServiceError('test error'))

# Create factory that returns the mock loop
mock_factory = MagicMock(spec=AgentLoopFactory)
mock_factory.create.return_value = mock_loop

# Create event publisher
mock_publisher = MagicMock()
mock_publisher.publish_agent_update = mock_publish_agent_update

runner = AgentModeRunner(
    agent_loop_factory=mock_factory,
    event_publisher=mock_publisher,
)

# Create a mock session
mock_session = MagicMock(spec=Session)
mock_session.id = 'test-session'
mock_session.user_email = 'test@test.com'
mock_session.context = {'files': {}}
mock_session.history = MagicMock()

async def test():
    try:
        await runner.run(
            session=mock_session,
            model='test-model',
            messages=[],
            selected_tools=[],
            selected_data_sources=[],
            max_steps=5,
            temperature=0.7,
        )
        assert False, 'Should have raised'
    except LLMServiceError:
        pass

    # Verify agent_completion was sent
    completion = [e for e in completion_events if e.get('update_type') == 'agent_completion']
    assert len(completion) == 1, f'Expected 1 agent_completion event, got {len(completion)}'
    assert completion[0]['steps'] == 0, 'Steps should be 0 on error'
    print('  AgentModeRunner sends agent_completion before re-raising: OK')

asyncio.run(test())
print('AgentModeRunner error cleanup works correctly')
"; then
    pass "AgentModeRunner sends agent_completion on error"
else
    fail "AgentModeRunner does not send agent_completion on error"
fi

# -------------------------------------------------------------------
# 5. Verify frontend error handler resets agent state
# -------------------------------------------------------------------
echo ""
echo "--- Test 5: Frontend error handler resets agent state ---"
HANDLER_FILE="$PROJECT_ROOT/frontend/src/handlers/chat/websocketHandlers.js"
if grep -A 5 "case 'error':" "$HANDLER_FILE" | grep -q "setCurrentAgentStep(0)"; then
    pass "Frontend error handler resets currentAgentStep"
else
    fail "Frontend error handler does not reset currentAgentStep"
fi

if grep -A 5 "case 'error':" "$HANDLER_FILE" | grep -q "setAgentPendingQuestion"; then
    pass "Frontend error handler clears agent pending question"
else
    fail "Frontend error handler does not clear agent pending question"
fi

# -------------------------------------------------------------------
# 6. Verify frontend thinking timeout exists
# -------------------------------------------------------------------
echo ""
echo "--- Test 6: Frontend has thinking timeout ---"
CONTEXT_FILE="$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx"
if grep -q "THINKING_TIMEOUT_MS" "$CONTEXT_FILE"; then
    pass "Frontend has thinking timeout constant"
else
    fail "Frontend missing thinking timeout"
fi

if grep -q "thinkingTimeoutRef" "$CONTEXT_FILE"; then
    pass "Frontend has thinking timeout ref and effect"
else
    fail "Frontend missing thinking timeout implementation"
fi

# -------------------------------------------------------------------
# 7. Behavioral: string-based error detection (keyword fallback)
# -------------------------------------------------------------------
echo ""
echo "--- Test 7: _raise_llm_domain_error detects errors by string content ---"
if python -c "
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.domain.errors import RateLimitError, LLMTimeoutError, LLMAuthenticationError

# Rate limit via string (not litellm type)
try:
    LiteLLMCaller._raise_llm_domain_error(Exception('rate limit exceeded for this model'))
    assert False
except RateLimitError:
    print('  String \"rate limit\" -> RateLimitError: OK')

# Timeout via string
try:
    LiteLLMCaller._raise_llm_domain_error(Exception('request timeout after 30s'))
    assert False
except LLMTimeoutError:
    print('  String \"timeout\" -> LLMTimeoutError: OK')

# Auth via string
try:
    LiteLLMCaller._raise_llm_domain_error(Exception('invalid api key provided'))
    assert False
except LLMAuthenticationError:
    print('  String \"invalid api key\" -> LLMAuthenticationError: OK')

print('String-based error detection works correctly')
"; then
    pass "String-based error detection works as fallback"
else
    fail "String-based error detection is broken"
fi

# -------------------------------------------------------------------
# 8. Run backend unit tests
# -------------------------------------------------------------------
echo ""
echo "--- Test 8: Backend unit tests ---"
cd "$PROJECT_ROOT"
if bash test/run_tests.sh backend; then
    pass "Backend unit tests pass"
else
    fail "Backend unit tests failed"
fi

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
echo ""
echo "============================================"
echo "RESULTS: $PASSED passed, $FAILED failed"
echo "============================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
