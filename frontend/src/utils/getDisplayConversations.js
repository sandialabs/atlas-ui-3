/**
 * Builds the display list for the conversation sidebar.
 *
 * Handles three states:
 * 1. Normal: conversation exists in the fetched list - show as-is
 * 2. Optimistic: backend hasn't confirmed save yet - show "Saving..." entry
 * 3. Bridge: backend confirmed save (activeConversationId set) but fetched
 *    list hasn't caught up yet - show placeholder with real ID so the
 *    conversation doesn't disappear from the sidebar (GH #354 fix)
 */
export function getDisplayConversations({
  conversations,
  messages,
  activeConversationId,
  chatHistoryEnabled,
  saveMode,
  // Backward compat: accept isIncognito and derive saveMode from it
  isIncognito,
  // Parallel conversation runs (issue #884): conversation_id -> run record.
  runsByConversation,
}) {
  const list = [...conversations]
  const userMessages = messages?.filter(m => m.role === 'user') || []
  // Resolve effective save mode (support old callers passing isIncognito)
  const effectiveMode = saveMode ?? (isIncognito ? 'none' : 'server')

  if (userMessages.length > 0 && chatHistoryEnabled && effectiveMode !== 'none') {
    const firstUserMsg = userMessages[0]?.content || ''
    const title = firstUserMsg.substring(0, 200)

    if (!activeConversationId) {
      // Backend hasn't confirmed save yet - show optimistic "Saving..." entry
      const alreadyInList = list.some(c => c.title === title)
      if (!alreadyInList) {
        list.unshift({
          id: '__current__',
          title: title || 'New conversation',
          preview: 'Saving...',
          updated_at: new Date().toISOString(),
          message_count: messages.length,
          _optimistic: true,
        })
      }
    } else if (!list.some(c => c.id === activeConversationId)) {
      // Backend confirmed save but the fetched list hasn't caught up yet -
      // show the current conversation so it doesn't disappear from the sidebar
      list.unshift({
        id: activeConversationId,
        title: title || 'New conversation',
        preview: '',
        updated_at: new Date().toISOString(),
        message_count: messages.length,
        _current: true,
      })
    }
  }

  // A run keeps executing after the user starts a new chat or opens another
  // conversation, but its conversation is not stored until the turn ends.
  // Without a row here it would vanish from the list the moment the user
  // navigated away -- along with the only visible sign that it is running.
  // Server save mode only: the rows carry server conversation ids, which do
  // not resolve in a local (IndexedDB) history list.
  if (chatHistoryEnabled && effectiveMode === 'server' && runsByConversation) {
    for (const run of Object.values(runsByConversation)) {
      if (!run || !run.conversation_id) continue
      if (['completed', 'failed', 'cancelled'].includes(run.status)) continue
      if (list.some(c => c.id === run.conversation_id)) continue
      list.unshift({
        id: run.conversation_id,
        title: run.title || 'Conversation in progress',
        preview: '',
        updated_at: new Date((run.created_at || Date.now() / 1000) * 1000).toISOString(),
        message_count: 0,
        _run: true,
      })
    }
  }
  return list
}
