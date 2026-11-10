import { useReducer, useCallback } from 'react'
import { currentTurnProgress, isOpenLegacyRow } from '../../utils/executionState'

// The marker-only seed (issue #957) stands for "the run is still going"; it
// has no text, so it belongs after every row of its turn. Rows that arrive
// after it -- a tool row, an approval card, a snapshot reconcile -- would
// otherwise leave the marker above them. A real user message ends the turn:
// the seed is never moved past one (the send path discards it instead).
function reseatSeed(state) {
  const idx = state.findIndex(m => m._seed)
  if (idx < 0 || idx === state.length - 1) return state
  const after = state.slice(idx + 1)
  if (after.some(m => m.role === 'user' && !m._agentInput)) return state
  return [...state.slice(0, idx), ...after, state[idx]]
}

// The seed's "answer in progress" notice describes the snapshot the view was
// opened on. Once the turn has moved past that snapshot -- a tool row landed
// or settled, a narration row was appended -- the notice is obsolete: the run
// is still going, but nothing on screen is an unfinished answer (issue #1193).
// `_stepAdvanced` records that on the seed itself, so it holds on every path
// that changes the transcript (live frames, the mid-run poll, a snapshot
// reconcile) without each of them having to say so. It is sticky: a row that
// is later dropped (a stale approval card) does not bring the notice back.
//
// The measure can also fall before the seed has advanced: a snapshot drops a
// stale approval card, or a user row lands and starts the count over. The
// baseline follows it down, so the next row still counts as a step instead
// of having to climb back past a mark the transcript no longer reaches.
function markSeedAdvanced(state) {
  const idx = state.findLastIndex(m => m._seed)
  if (idx < 0 || state[idx]._stepAdvanced) return state
  const progress = currentTurnProgress(state)
  const baseline = state[idx]._seedProgress ?? 0
  if (progress === baseline) return state
  const updated = [...state]
  updated[idx] = progress > baseline
    ? { ...state[idx], _stepAdvanced: true }
    : { ...state[idx], _seedProgress: progress }
  return updated
}

function messagesReducer(state, action) {
  const next = reduceMessages(state, action)
  return next === state || !Array.isArray(next) ? next : markSeedAdvanced(next)
}

function reduceMessages(state, action) {
  switch (action.type) {
    case 'ADD':
      return reseatSeed([...state, action.message])
    case 'BULK_ADD':
      return reseatSeed([...state, ...action.messages])
    case 'UPDATE_TOOL_RESULT': {
      const patch = action.patch || {}
      if (patch.invocation_id) {
        const exact = state.some(m => m.invocation_id === patch.invocation_id)
        return state.map(m => {
          if (m.invocation_id === patch.invocation_id) return { ...m, ...patch }
          if (!exact && isOpenLegacyRow(m) && m.tool_call_id === action.tool_call_id) return { ...m, ...patch }
          return m
        })
      }
      return state.map(m => (
        m.tool_call_id === action.tool_call_id && !m.invocation_id ? { ...m, ...patch } : m
      ))
    }
    case 'MAP': {
      const next = action.mapper(state)
      return Array.isArray(next) ? reseatSeed(next) : next
    }
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
        if (replayed._seed) {
          // Where the turn stood when this seed was made; see markSeedAdvanced.
          replayed._seedProgress = currentTurnProgress(state)
          replayed._stepAdvanced = false
        }
        // First target a placeholder (the seed or an earlier replay): the
        // replay is a newer snapshot of the same segment and defines it.
        const current = state.findLastIndex(m => m._streaming && m._replayed)
        // A marker-only seed holds no text, so its position means nothing:
        // tool rows (an atlas_sleep, an approval card) can land after it
        // while the run is between segments. Filling it in place would put
        // the next segment's text above those rows, so it moves to the end.
        if (current >= 0 && state[current]._seed) {
          // A seed refreshed with another empty snapshot is the same seed: it
          // keeps the progress it was measured against, or every poll would
          // bring an obsolete notice back.
          const carried = replayed._seed
            ? { _seedProgress: state[current]._seedProgress, _stepAdvanced: state[current]._stepAdvanced }
            : null
          return [...state.filter((_, i) => i !== current), { ...replayed, ...carried }]
        }
        if (current >= 0) {
          const updated = [...state]
          // A text placeholder emptied by a newer snapshot: the segment it
          // held has closed and the run has moved on to its next step, so
          // the seed that takes its place starts out advanced.
          updated[current] = { ...state[current], ...replayed, ...(replayed._seed ? { _stepAdvanced: true } : null) }
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
    case 'REFRESH_APPEND': {
      // The joined-run refresh (issue #959) appending the tail the view is
      // missing. Every open streaming bubble is transient: the stored rows
      // being appended are the run's finished output and supersede it,
      // whether it is a replay placeholder or a live partial this tab was
      // streaming. They are dropped in the same dispatch as the append, so
      // the list never renders a half-written answer beside the whole one.
      //
      // `keepStreaming` is the still-in-flight case: another run is writing
      // into this conversation, so its open bubble is still the honest thing
      // to show. It is moved after the appended rows rather than left where
      // it was, or the finished rows would render underneath a bubble that
      // goes on filling above them.
      const streaming = state.filter(m => m._streaming)
      const settled = state.filter(m => !m._streaming)
      const next = [...settled, ...action.rows]
      return action.keepStreaming ? [...next, ...streaming] : next
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
  const refreshAppend = useCallback((rows, keepStreaming = false) => dispatch({ type: 'REFRESH_APPEND', rows, keepStreaming }), [])

  return { messages, addMessage, bulkAdd, mapMessages, updateToolResult, resetMessages, streamToken, streamEnd, discardReplayPlaceholders, refreshAppend }
}
