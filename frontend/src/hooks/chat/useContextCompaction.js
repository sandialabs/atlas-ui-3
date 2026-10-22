import { useCallback, useEffect, useRef, useState } from 'react'

// Context tracking and compaction (issue #877).
//
// Holds the latest `context_usage` estimate the server sent for the
// conversation on screen, the state of any compaction in progress, and the
// per-conversation automatic-compaction toggle. The toggle is remembered per
// conversation in localStorage; an unsaved chat keeps its choice in memory and
// carries it over once the server assigns the conversation an id.

const STORAGE_KEY = 'atlas.autoCompactByConversation'
const MAX_REMEMBERED = 200

function readToggles() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeToggle(conversationId, value) {
  try {
    const toggles = readToggles()
    delete toggles[conversationId]
    toggles[conversationId] = value
    const keys = Object.keys(toggles)
    for (const stale of keys.slice(0, Math.max(0, keys.length - MAX_REMEMBERED))) delete toggles[stale]
    localStorage.setItem(STORAGE_KEY, JSON.stringify(toggles))
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
}

// Parse `/compact` and `/compact <focus>`. Returns null for anything else.
export function parseCompactCommand(text) {
  if (typeof text !== 'string') return null
  const match = /^\/compact(?:\s+([\s\S]*))?$/.exec(text.trim())
  if (!match) return null
  return { focus: (match[1] || '').trim() }
}

export function useContextCompaction({ config, activeConversationId, sendMessage, buildRequestFields }) {
  const defaultAuto = config?.auto_default !== false
  const enabled = config?.enabled !== false
  const [usage, setUsage] = useState(null)
  const [compaction, setCompaction] = useState(null)
  const [draftAuto, setDraftAuto] = useState(null)
  const [autoCompact, setAutoCompactState] = useState(defaultAuto)
  const previousConversation = useRef(activeConversationId)

  // Follow the conversation on screen: its remembered toggle, or the
  // unsaved chat's choice when this is the id the server just assigned it.
  useEffect(() => {
    const previous = previousConversation.current
    previousConversation.current = activeConversationId
    if (!activeConversationId) {
      setAutoCompactState(draftAuto ?? defaultAuto)
      return
    }
    const stored = readToggles()[activeConversationId]
    if (typeof stored === 'boolean') {
      setAutoCompactState(stored)
    } else if (!previous && draftAuto !== null) {
      writeToggle(activeConversationId, draftAuto)
      setAutoCompactState(draftAuto)
    } else {
      setAutoCompactState(defaultAuto)
    }
    if (previous !== activeConversationId) setDraftAuto(null)
  }, [activeConversationId, defaultAuto]) // eslint-disable-line react-hooks/exhaustive-deps

  // A different conversation has a different context.
  useEffect(() => {
    setUsage(null)
    setCompaction(null)
  }, [activeConversationId])

  const setAutoCompact = useCallback((value) => {
    const next = !!value
    setAutoCompactState(next)
    if (activeConversationId) writeToggle(activeConversationId, next)
    else setDraftAuto(next)
  }, [activeConversationId])

  const handleUsage = useCallback((frame) => {
    if (!frame || typeof frame.estimated_tokens !== 'number') return
    // An estimate for a conversation the user has already left.
    if (frame.conversation_id && activeConversationId && frame.conversation_id !== activeConversationId) return
    setUsage(frame)
  }, [activeConversationId])

  const handleCompaction = useCallback((frame) => {
    if (!frame || !frame.status) return
    // A run compacting in another conversation leaves this one's state alone.
    if (frame.conversation_id && activeConversationId && frame.conversation_id !== activeConversationId) return
    setCompaction(frame)
  }, [activeConversationId])

  // Stop or a dropped connection ends a compaction whose result frame may
  // never arrive; the server-side work is cancelled either way.
  const clearPending = useCallback(() => {
    setCompaction(current => (current?.status === 'started' ? null : current))
  }, [])

  const isCompacting = compaction?.status === 'started'
  // A manual compaction owns the conversation until it ends: a message sent
  // meanwhile would race it. (An agent run's own compaction does not block
  // steering; the run drains steering at its next step.)
  const isManualCompacting = isCompacting && compaction?.trigger === 'manual'

  const compactNow = useCallback((focus = '') => {
    if (!enabled || typeof sendMessage !== 'function') return false
    const sent = sendMessage({
      type: 'compact_context',
      focus: focus || undefined,
      auto_compact: autoCompact,
      ...(typeof buildRequestFields === 'function' ? buildRequestFields() : {}),
    })
    if (sent) setCompaction({ status: 'started', trigger: 'manual', scope: 'conversation' })
    return sent
  }, [enabled, sendMessage, autoCompact, buildRequestFields])

  const requestUsage = useCallback(() => {
    if (!enabled || typeof sendMessage !== 'function') return false
    return sendMessage({
      type: 'context_usage_request',
      auto_compact: autoCompact,
      ...(typeof buildRequestFields === 'function' ? buildRequestFields() : {}),
    })
  }, [enabled, sendMessage, autoCompact, buildRequestFields])

  return {
    enabled,
    usage,
    compaction,
    isCompacting,
    isManualCompacting,
    autoCompact,
    setAutoCompact,
    compactNow,
    requestUsage,
    handleUsage,
    handleCompaction,
    clearPending,
  }
}
