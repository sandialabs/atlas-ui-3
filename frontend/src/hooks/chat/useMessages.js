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

  return { messages, addMessage, bulkAdd, mapMessages, updateToolResult, resetMessages }
}
