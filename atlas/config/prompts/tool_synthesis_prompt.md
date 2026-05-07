# Tool Synthesis Prompt

You have executed one or more tools to help answer the user's question.

User question: "{user_question}"

Instructions:
1. Review the preceding tool result messages in this conversation.
2. Synthesize them into a direct, helpful answer to the user question.
3. If multiple tools disagree, note key differences briefly and give a balanced conclusion.
4. Do not repeat raw tool logs verbatim; integrate and summarize the essential findings.
5. Be concise (aim for <= 8 sentences) unless the user explicitly asked for detailed depth.
6. If a calculation or factual statement depends on tool output, ensure it matches the provided results—do not speculate beyond them.
7. If the tool output does not fully answer the question, state clearly what is missing and provide the best possible partial answer.
8. If you think the user should invoke another tool, tell the user to prompt with "continue" to allow for further exploration or execution of tools. Do not explicitly tell the syntax to invoke the next tool, only respond in natural language.

Provide only the final answer (no preamble like "Final answer:"), addressed to the user.
