```markdown
# Error Flow Diagram

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
		      │              │  - user_msg: "The AI service  │
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
│    "message": "The AI service is experiencing high traffic...",      │
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
│  "Error: The AI service is experiencing high traffic.                │
│   Please try again in a moment."                                     │
│                                                                      │
│  [User can see the error and knows what to do]                       │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Points

1. **Error Classification**: The `classify_llm_error()` function examines the exception type and message to determine the appropriate error category.

2. **User-Friendly Messages**: Technical errors are translated into helpful, actionable messages for users.

3. **Detailed Logging**: Full error details are logged for debugging purposes (not shown to users).

4. **Error Type Field**: The `error_type` field allows the frontend to potentially handle different error types differently in the future (e.g., automatic retry for timeouts).

5. **No Sensitive Data Exposure**: API keys, stack traces, and other sensitive information are never sent to the frontend.
```

