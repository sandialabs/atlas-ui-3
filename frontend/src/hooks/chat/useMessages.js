import { useReducer, useCallback } from 'react'

function messagesReducer(state, action) {
  switch (action.type) {
    case 'ADD':
      return [...state, action.message]
    case 'BULK_ADD':
      return [...state, ...action.messages]
    case 'UPDATE_TOOL_RESULT':
      return state.map(m => m.tool_call_id === action.tool_call_id ? { ...m, ...action.patch } : m)
    case 'MAP':
      return action.mapper(state)
    case 'RESET':
      return []
    case 'STREAM_TOKEN': {
      const last = state[state.length - 1]
      if (last && last._streaming) {
        // Append token to existing streaming message
        const updated = [...state]
        updated[updated.length - 1] = { ...last, content: last.content + action.token }
        return updated
      }
      // Create new streaming assistant message
      return [...state, {
        role: 'assistant',
        content: action.token,
        timestamp: new Date().toISOString(),
        _streaming: true,
      }]
    }
    case 'STREAM_END': {
      const last = state[state.length - 1]
      if (last && last._streaming) {
        const updated = [...state]
        updated[updated.length - 1] = { ...last, _streaming: false }
        return updated
      }
      return state
    }
    default:
      return state
  }
}

export function useMessages() {
  const [messages, dispatch] = useReducer(messagesReducer, [])

  const addMessage = useCallback(message => dispatch({ type: 'ADD', message }), [])
  const bulkAdd = useCallback(messages => dispatch({ type: 'BULK_ADD', messages }), [])
  const mapMessages = useCallback(mapper => dispatch({ type: 'MAP', mapper }), [])
  const updateToolResult = useCallback((tool_call_id, patch) => dispatch({ type: 'UPDATE_TOOL_RESULT', tool_call_id, patch }), [])
  const resetMessages = useCallback(() => dispatch({ type: 'RESET' }), [])
  const streamToken = useCallback(token => dispatch({ type: 'STREAM_TOKEN', token }), [])
  const streamEnd = useCallback(() => dispatch({ type: 'STREAM_END' }), [])

  return { messages, addMessage, bulkAdd, mapMessages, updateToolResult, resetMessages, streamToken, streamEnd }
}
