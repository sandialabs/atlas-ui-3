/**
 * Helpers for the fine-tune capture "correct this turn" affordance (issue #622).
 *
 * Correcting an assistant turn re-runs the *user* prompt that produced it,
 * forcing the chosen tool. To do that we need, for a given assistant message:
 *   - the 0-based rewind ordinal of the preceding user prompt (so the backend
 *     truncates its history to the same point the edit/rewind flow uses), and
 *   - the original prompt text to resubmit, and
 *   - the "rejected" turn (the wrong assistant text plus any tool calls it made)
 *     so the backend can store the (rejected, chosen) pair.
 *
 * The frontend renders tool calls as their own transcript rows (type
 * 'tool_call'), not as fields on the assistant message, so the rejected tool
 * calls are gathered from the rows between the preceding user prompt and the
 * next user prompt.
 */

import { isRewindableUserMessage } from './userMessageOrdinal'

// Build the correction context for the assistant message at `assistantIndex`,
// or null if it is not a correctable assistant turn (e.g. no preceding user
// prompt, or while streaming). `messages` is the full transcript array.
export function buildCorrectionContext(messages, assistantIndex) {
  const assistant = messages[assistantIndex]
  if (!assistant || assistant.role !== 'assistant' || assistant._streaming) {
    return null
  }

  // Walk back to the nearest rewindable user prompt and count its ordinal.
  let userIndexAtTurn = null
  let userOrdinal = -1
  for (let i = 0; i <= assistantIndex; i++) {
    if (isRewindableUserMessage(messages[i])) {
      userOrdinal += 1
      if (i <= assistantIndex) userIndexAtTurn = i
    }
  }
  if (userIndexAtTurn === null) return null

  const promptContent = messages[userIndexAtTurn].content || ''
  if (!promptContent.trim()) return null

  // Gather the rejected tool calls: tool_call rows from just after the user
  // prompt up to (but not including) the next rewindable user prompt.
  const toolCalls = []
  for (let i = userIndexAtTurn + 1; i < messages.length; i++) {
    const m = messages[i]
    if (isRewindableUserMessage(m)) break
    if (m && m.type === 'tool_call') {
      const name = m.server_name && m.tool_name
        ? `${m.server_name}_${m.tool_name}`
        : (m.tool_name || '')
      toolCalls.push({
        name,
        // arguments may be unavailable; send an empty object per the contract.
        arguments: m.arguments && typeof m.arguments === 'object' ? m.arguments : {},
      })
    }
  }

  return {
    userIndex: userOrdinal,
    content: promptContent,
    rejected: {
      assistant_message: assistant.content || '',
      tool_calls: toolCalls,
    },
  }
}

// Flatten the available tools config into selectable option objects. `tools` is
// the ChatContext `tools` array (Array<{ server, tools: string[] }>). The
// returned `value` matches the "server_toolname" form used in selected_tools.
export function flattenAvailableTools(tools) {
  if (!Array.isArray(tools)) return []
  const options = []
  for (const server of tools) {
    const serverName = server?.server
    const names = Array.isArray(server?.tools) ? server.tools : []
    for (const toolName of names) {
      options.push({
        value: serverName ? `${serverName}_${toolName}` : toolName,
        label: serverName ? `${toolName} (${serverName})` : toolName,
      })
    }
  }
  return options
}
