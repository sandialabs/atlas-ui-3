# Message Types Sent to UI via Update Callback

During chat interactions involving tool use, the backend sends real-time streaming updates to the frontend via an `update_callback`. These messages are JSON objects with a `type` field indicating the event.

## Message Types

- `tool_start`  
  Sent when a tool begins execution.  
  Payload includes: `tool_call_id`, `tool_name`

- `tool_complete`  
  Sent when a tool completes successfully.  
  Payload includes: `tool_call_id`, `tool_name`, `success` (boolean)

- `tool_error`  
  Sent when a tool execution fails.  
  Payload includes: `tool_call_id`, `tool_name`, `error` (string message)

- `tool_synthesis`  
  Sent when the LLM returns a final synthesized response after tool execution.  
  Payload includes: `message` (string content)

- `response_complete`  
  Sent when the entire response process is finished.  
  No additional payload fields.
