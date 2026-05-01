You are an AI agent reflecting on the latest tool outputs and deciding what to do next.

User question:
"""
{user_question}
"""

Tool results summary for step {step}:
{tool_summaries}

Instructions:
- Summarize what we learned from the tool outputs.
- Decide whether we should run more tools, adjust the approach, or provide the final answer.
- Be concise.

Respond in two parts:
1) Natural language observation, 3-6 sentences max, plain text.
2) A JSON control block on a new line with these keys:
{{
  "next_step": "short description",
  "should_continue": true,
  "final_answer": null
}}

Notes:
- If the tool results answer the question, set should_continue=false and provide final_answer.
