```markdown
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
carrying the implicated tool names. If the provider's text names a tool from the
request, only that tool is reported; otherwise every tool in the request is
listed so the user can bisect.

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

Two ordering constraints matter here:

- `litellm.ContextWindowExceededError` subclasses `BadRequestError`, so the
  context-window check must run first or long conversations get misreported as
  tool failures.
- `classify_llm_error()` short-circuits on `LLMBadRequestError` before its
  keyword matching. Without that, `str(error)` would be the already-built
  user-facing message, and the keyword rules would classify the message text
  rather than the original failure.

## Key Points

1. **Error Classification**: The `classify_llm_error()` function examines the exception type and message to determine the appropriate error category.

2. **User-Friendly Messages**: Technical errors are translated into helpful, actionable messages for users.

3. **Detailed Logging**: Full error details are logged for debugging purposes (not shown to users).

4. **Error Type Field**: The `error_type` field allows the frontend to potentially handle different error types differently in the future (e.g., automatic retry for timeouts). `error_type_for()` in `error_handler.py` maps a domain error class to the same vocabulary the WebSocket handler in `main.py` uses, so every error frame carries one regardless of which path raised it.

5. **No Sensitive Data Exposure**: API keys, stack traces, and other sensitive information are never sent to the frontend.
```

