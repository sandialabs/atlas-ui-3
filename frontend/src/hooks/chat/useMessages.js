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
      // A replayed stream (issue #957) defines the text rather than adding to
      // it: the transcript being loaded may already hold an earlier snapshot
      // of the same segment, and appending would duplicate the overlap.
      if (action.replace) {
        const replayed = {
          role: 'assistant',
          content: action.token,
          timestamp: new Date().toISOString(),
          _streaming: true,
          _replayed: true,
          // An empty replace is the marker-only seed for a run parked between
          // segments: it must not absorb a later segment's tokens (they would
          // render above any tool rows appended in between), so the append
          // branch below skips it and the first real token replaces it.
          _seed: !action.token,
        }
        // First target a placeholder (the seed or an earlier replay): the
        // replay is a newer snapshot of the same segment and defines it.
        const current = state.findLastIndex(m => m._streaming && m._replayed)
        if (current >= 0) {
          const updated = [...state]
          updated[current] = { ...state[current], ...replayed }
          return updated
        }
        // A live token can land before the replay frame and flip the bubble
        // live. Dropping the replay then would leave a hole in the middle of
        // the answer (the tokens between the bubble's content and the next
        // live frame), so extend a live bubble the replay strictly
        // continues: same segment (its content is a prefix), newer snapshot
        // (strictly longer). The bubble stays live-owned -- no _replayed
        // flag, so it is not hidden from the persistence paths.
        const live = state.findLastIndex(
          m => m._streaming && action.token.startsWith(m.content) && action.token.length > m.content.length
        )
        if (live >= 0) {
          const updated = [...state]
          updated[live] = { ...state[live], content: action.token }
          return updated
        }
        // No placeholder and no continuable live bubble: a live stream owned
        // by this tab means the view moved past the replay's segment, so the
        // late frame is dropped rather than clobbering it.
        if (state.some(m => m._streaming)) return state
        return [...state, replayed]
      }
      // Find the streaming message anywhere in the array (not just last)
      // to handle interleaved tool_start/progress messages mid-stream. A
      // marker-only seed is skipped: the segment that is starting belongs in
      // a fresh bubble at the end, after whatever rows landed meanwhile.
      const idx = state.findLastIndex(m => m._streaming && !m._seed)
      if (idx >= 0) {
        const updated = [...state]
        // A live token landing on a replayed bubble means this tab owns the
        // stream after all: the view is refreshing itself, so the "it will
        // refresh when the response finishes" marker (bound to _replayed) no
        // longer applies. A tab that receives no live frames keeps it.
        updated[idx] = { ...state[idx], content: state[idx].content + action.token, _replayed: false }
        return updated
      }
      // Create new streaming assistant message. Any marker-only seed is
      // dropped now that real text exists to take its place.
      return [...state.filter(m => !m._seed), {
        role: 'assistant',
        content: action.token,
        timestamp: new Date().toISOString(),
        _streaming: true,
      }]
    }
    case 'DISCARD_REPLAY_PLACEHOLDERS': {
      // A replay placeholder is a transient mid-answer fragment the run's
      // stored transcript supersedes (issue #957). Discarding is NOT closing:
      // STREAM_END on a fragment would clear _replayed and let the
      // persistence paths write the partial text into history as if it were
      // the finished reply. Only genuinely live rows survive this.
      return state.filter(m => !(m._streaming && m._replayed))
    }
    case 'STREAM_END': {
      const idx = state.findLastIndex(m => m._streaming)
      if (idx >= 0) {
        // An empty streaming row is the replay placeholder (issue #957): the
        // seed for a run that is between segments. Closing it must remove the
        // row, not freeze it -- a blank assistant bubble that never fills is
        // not a message.
        if (!state[idx].content) {
          return state.filter((_, i) => i !== idx)
        }
        const updated = [...state]
        // The reply is complete now, so the row is no longer a placeholder:
        // keeping _replayed would have every persistence path (autosave,
        // undo, export) drop a finished answer from local history.
        updated[idx] = { ...state[idx], _streaming: false, _replayed: false }
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
  const streamToken = useCallback((token, replace = false) => dispatch({ type: 'STREAM_TOKEN', token, replace }), [])
  const streamEnd = useCallback(() => dispatch({ type: 'STREAM_END' }), [])
  const discardReplayPlaceholders = useCallback(() => dispatch({ type: 'DISCARD_REPLAY_PLACEHOLDERS' }), [])

  return { messages, addMessage, bulkAdd, mapMessages, updateToolResult, resetMessages, streamToken, streamEnd, discardReplayPlaceholders }
}
