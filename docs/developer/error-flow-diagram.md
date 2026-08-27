# Error Flow Diagram

Last updated: 2026-07-24

## Complete Error Handling Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER SENDS MESSAGE                           │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    WebSocket Handler (main.py)                       │
│                  handle_chat() async function                        │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   ChatService.handle_chat_message()                  │
│                      (service.py)                                    │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    ChatOrchestrator.execute()                        │
│                     (orchestrator.py)                                │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   ToolsModeRunner.run()                              │
│                      (modes/tools.py)                                │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│           error_utils.safe_call_llm_with_tools()                     │
│              (utilities/error_utils.py)                              │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  LLMCaller.call_with_tools()                         │
│                  (modules/llm/litellm_caller.py)                     │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         LiteLLM Library                              │
│                  (calls Cerebras/OpenAI/etc.)                        │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
		      ┌─────────────┴─────────────┐
		      │                           │
	      ┌──────▼───────┐          ┌───────▼────────┐
	      │   SUCCESS    │          │     ERROR      │
	      │  (200 OK)    │          │  (Rate Limit)  │
	      └──────┬───────┘          └───────┬────────┘
		      │                           │
		      │                           ▼
		      │              ┌──────────────────────────────┐
		      │              │  Exception: RateLimitError   │
		      │              │  "We're experiencing high    │
		      │              │   traffic right now!"        │
		      │              └──────────┬───────────────────┘
		      │                         │
		      │                         ▼
		      │              ┌──────────────────────────────┐
		      │              │ error_utils.classify_llm_    │
		      │              │       error(exception)        │
		      │              │                               │
		      │              │  Returns:                     │
		      │              │  - error_class: RateLimitError│
		      │              │  - user_msg: "The LLM service  │
		      │              │    is experiencing high       │
		      │              │    traffic..."                │
		      │              │  - log_msg: Full details      │
		      │              └──────────┬───────────────────┘
		      │                         │
		      │                         ▼
		      │              ┌──────────────────────────────┐
		      │              │ Raise RateLimitError(user_msg)│
		      │              └──────────┬───────────────────┘
		      │                         │
		      │                         ▼
┌───────────────────┴─────────────────────────┴─────────────────────┐
│             Back to WebSocket Handler (main.py)                    │
│                    Exception Catching                              │
└────────────────────────────────────────────────────────────────────┘
				      │
		      ┌─────────────┴─────────────┐
		      │                           │
	      ┌──────▼────────┐        ┌────────▼────────────┐
	      │ except         │        │ except              │
	      │ RateLimitError │        │ LLMTimeoutError     │
	      │                │        │ LLMAuth...Error     │
	      │ Send to user:  │        │ ValidationError     │
	      │ {              │        │ etc.                │
	      │  type: "error",│        │                     │
	      │  message: user │        │ Send appropriate    │
	      │   friendly msg,│        │ message to user     │
	      │  error_type:   │        │                     │
	      │   "rate_limit" │        │                     │
	      │ }              │        │                     │
	      └───────┬────────┘        └────────┬────────────┘
			│                          │
			└──────────┬───────────────┘
				    │
				    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       WebSocket Message Sent                         │
│  {                                                                   │
│    "type": "error",                                                  │
│    "message": "The LLM service is experiencing high traffic...",      │
│    "error_type": "rate_limit"                                        │
│  }                                                                   │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│               Frontend (websocketHandlers.js)                        │
│                                                                      │
│  case 'error':                                                       │
│    setIsThinking(false)                                              │
│    addMessage({                                                      │
│      role: 'system',                                                 │
│      content: `Error: ${data.message}`,                              │
│      timestamp: new Date().toISOString()                             │
│    })                                                                │
└─────────────────────────────────────────────────────────────────────┘
				      │
				      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      UI DISPLAYS ERROR                               │
│                                                                      │
│  System Message:                                                     │
│  "Error: The LLM service is experiencing high traffic.                │
│   Please try again in a moment."                                     │
│                                                                      │
│  [User can see the error and knows what to do]                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Provider Rejections Caused by a Tool Definition

A malformed tool definition makes the provider reject the whole request, so no
tool ever executes and the turn fails. Per-tool error isolation does not apply,
because the failure happens before any tool runs. The provider names the
function it objected to, and that name is what the user needs in order to
deselect the right tool.

`_raise_llm_domain_error()` raises `LLMBadRequestError` for `BadRequestError`,
carrying the implicated tool names. Attribution needs positive evidence, because
most 400s are not about tools at all — an out-of-range parameter or a malformed
message history is a `BadRequestError` too, and pointing those at the tool
selection sends the user to disable tools that were never at fault. Three cases:

| Provider text | Reported |
| --- | --- |
| names a tool from the request (whole-token match) | just that tool |
| refers to the tool payload (`tool`, `tools`, `function`, `tool_choice`, …) but names no tool | every tool in the request, to bisect |
| refers to the message history (`messages`, `tool_call_id`, `role`) | no tool — deselecting tools cannot fix a bad conversation |
| anything else | no tool; a plain "the provider rejected this request" |

```
litellm.BadRequestError
	  │  "Invalid schema for function 'safety_docs_plan': ..."
	  ▼
LLMBadRequestError(tool_names=["safety_docs_plan"])
	  │
	  ▼
{
  "type": "error",
  "message": "The model provider rejected this request because of the tool
              'safety_docs_plan'. Turn that tool off and try again.",
  "error_type": "bad_request"
}
```

Three ordering constraints matter here:

- `_raise_llm_domain_error()` checks concrete exception types before falling
  back to message keywords. The keywords match the provider's text, and a
  rejection routinely quotes the offending schema — a tool named
  `timeout_checker` or `vault_api_key_lookup` would otherwise be raised as a
  timeout or an auth failure and lose its attribution.
- `litellm.ContextWindowExceededError` subclasses `BadRequestError`, so the
  context-window check must run first or long conversations get misreported as
  tool failures.
- `classify_llm_error()` short-circuits on `LLMBadRequestError` before its
  keyword matching. Without that, `str(error)` would be the already-built
  user-facing message, and the keyword rules would classify the message text
  rather than the original failure.

`_is_retryable_error()` returns `False` for `BadRequestError` before its
transient-keyword tests, for the same reason: a 400 is deterministic, and one
that happens to contain `timeout` would otherwise be retried three times with
backoff before failing.

Both `call_with_rag()` and `call_with_rag_and_tools()` fall back to a simpler
call when RAG breaks, so their passthrough tuples catch `LLMError` — a provider
rejection is not a RAG failure, and without the passthrough the same rejected
request is sent a second time, the injected RAG context is discarded, and the
logs blame RAG. The tuples name the *base* class rather than enumerating its
subclasses so a newly added error cannot be silently downgraded into that
fallback. `LLMAuthenticationError` and `DataSourcePermissionError` sit outside
the `LLMError` hierarchy and stay listed explicitly.

## Malformed tool calls (`malformed_tool_call`)

A model that runs out of output tokens partway through a tool call leaves an
`arguments` fragment that is not valid JSON. Providers re-parse every tool call
in the message history on each request, so persisting one poisons the whole
conversation: every later turn comes back as a 400 (`Unterminated string
starting at: line 1 column 73`) that no retry can clear, because the fault is in
the history rather than the request.

`partition_tool_calls_by_json_validity()` therefore drops unparseable calls
where they are accumulated, on both the streaming and non-streaming paths,
before they can be executed or written to history. Two rules:

- A call whose arguments do not parse is always dropped.
- When `finish_reason == "length"`, the **last** call is also dropped if its
  arguments are empty — truncation before the first argument delta parses fine
  as "no arguments" and would otherwise execute with `{}`. Earlier calls in the
  same response completed before the limit, so a genuine no-argument call among
  them is honoured.

When well-formed calls survive, the turn continues with those and the model can
reissue the rest. When none do, the turn fails with `LLMMalformedToolCallError`.
Unlike `LLMBadRequestError` this failure is **retryable** — the same turn
usually succeeds on a second attempt — and the message says so, naming the token
limit only when `finish_reason` is evidence of one.

`classify_llm_error()` short-circuits on it for the same reason it does on
`LLMBadRequestError`: the message is already user-facing.

Both streaming consumers (`ToolsModeRunner.run_streaming` and
`AgenticLoop._call_llm_streaming`) suppress a mid-stream error once text has
been streamed, on the reasoning that partial output beats none. This error is
exempt: the model announced work it could not perform, and reporting the
narration as a finished answer would hide the gap.

The executor's `_try_repair_json()` deliberately does **not** overlap with this
guard. It balances missing braces and nothing more — a repair may complete the
shape of an object, never the content of a value. Closing an open string turned
the production fragment into a different, valid-looking filename that the tool
would have executed against.

## Key Points

1. **Error Classification**: The `classify_llm_error()` function examines the exception type and message to determine the appropriate error category.

2. **User-Friendly Messages**: Technical errors are translated into helpful, actionable messages for users.

3. **Detailed Logging**: Full error details are logged for debugging purposes (not shown to users).

4. **Error Type Field**: The `error_type` field allows the frontend to potentially handle different error types differently in the future (e.g., automatic retry for timeouts). `error_type_for()` in `error_handler.py` maps a domain error class to the same vocabulary the WebSocket handler in `main.py` uses, so every error frame carries one regardless of which path raised it.

5. **No Sensitive Data Exposure**: API keys, stack traces, and other sensitive information are never sent to the frontend.

