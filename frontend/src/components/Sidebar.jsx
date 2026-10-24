import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useConversationHistory } from '../hooks/useConversationHistory'
import { useLocalConversationHistory } from '../hooks/useLocalConversationHistory'
import { usePersistentState } from '../hooks/chat/usePersistentState'
import { ConversationTreeRow } from './ConversationTreeRow'
import ConversationLoadMore from './ConversationLoadMore'
import { getDisplayConversations } from '../utils/getDisplayConversations'
import { applyRunEndRefresh } from '../utils/runEndRefresh'
import { classificationLabel, classificationRefusal } from '../utils/conversationClassification'
import { useToast } from './ui/toastContext'

const ContextMenu = ({ x, y, onDelete, onClose }) => {
  const menuRef = useRef(null)

  useEffect(() => {
    const handleClickOutside = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) onClose()
    }
    const handleEscape = (e) => { if (e.key === 'Escape') onClose() }
    const handleScroll = () => onClose()

    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    document.addEventListener('scroll', handleScroll, true)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
      document.removeEventListener('scroll', handleScroll, true)
    }
  }, [onClose])

  return (
    <div
      ref={menuRef}
      className="fixed bg-gray-800 border border-gray-600 rounded shadow-lg py-1 z-[100]"
      style={{ left: x, top: y }}
    >
      <button
        onClick={onDelete}
        className="w-full text-left px-4 py-1.5 text-sm text-red-400 hover:bg-gray-700 transition-colors"
      >
        Delete Conversation
      </button>
    </div>
  )
}

const MIN_WIDTH = 200
const MAX_WIDTH = 480
const DEFAULT_WIDTH = 256

const Sidebar = ({ mobileOpen, onMobileClose }) => {
  const {
    features, activeConversationId, loadSavedConversation, refreshJoinedConversation, messages, saveMode, clearChat,
    newChatCount,
    // Parallel conversation runs (issue #884): which conversations are still
    // working, including ones the user is not currently viewing.
    runsByConversation,
    backgroundSaves,
    // A conversation opened while its run was in flight: the run's output
    // went to the transcript it was streaming into, not this view, so the
    // view is reloaded from the store once the run ends.
    runEndedConversationId, clearRunEndedConversation,
    // Conversation classification (issue #1042): a conversation opens only
    // under the level it was recorded at.
    activeComplianceFilter, complianceEnabled, complianceLevels,
    user,
  } = useChat()
  const toast = useToast()

  const chatHistoryEnabled = features?.chat_history
  const [sidebarWidth, setSidebarWidth] = usePersistentState('chatui-sidebar-width', DEFAULT_WIDTH)
  const [isCollapsed, setIsCollapsed] = usePersistentState('chatui-sidebar-collapsed', false)
  // Parent rows whose children are hidden (issue #1081). Persisted per user as
  // an array so a reload keeps that user's chosen shape of the tree.
  const [collapsedConversationIds, setCollapsedConversationIds] = usePersistentState(
    `chatui-collapsed-conversation-ids:${user || 'anonymous'}`, []
  )
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(null)
  const [contextMenu, setContextMenu] = useState(null)
  const [isResizing, setIsResizing] = useState(false)
  // The text in the search box. It is kept here so a tag filter can clear
  // it: the search on screen does not describe the tagged list.
  const [searchText, setSearchText] = useState('')
  const searchTimerRef = useRef(null)
  const prevMessageCountRef = useRef(0)
  const refreshTimerRef = useRef(null)
  const panelRef = useRef(null)

  // Both hooks are always called (React rules), but only the active one is used
  const serverHistory = useConversationHistory()
  // Locally saved conversations are per signed-in user (issue #1050).
  const localHistory = useLocalConversationHistory(user)
  const history = saveMode === 'local' ? localHistory : serverHistory

  // Fetch conversations on mount, when feature is enabled, or when save mode changes
  useEffect(() => {
    if (chatHistoryEnabled && saveMode !== 'none') {
      history.fetchConversations()
      history.fetchTags()
    }
  // `user`: a different account signed in sees its own local list.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatHistoryEnabled, saveMode, user])

  // Auto-refresh conversation list when messages change
  useEffect(() => {
    if (!chatHistoryEnabled || saveMode === 'none') return
    const currentCount = messages?.length || 0
    const prevCount = prevMessageCountRef.current
    prevMessageCountRef.current = currentCount

    if (currentCount > prevCount && currentCount > 0) {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = setTimeout(() => {
        history.refreshConversations(history.activeTag ? { tag: history.activeTag } : {})
      }, 1500)
    }

    if (currentCount === 0 && prevCount > 0) {
      history.refreshConversations(history.activeTag ? { tag: history.activeTag } : {})
    }

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages?.length, chatHistoryEnabled])

  // A run that finishes in a conversation the user is not looking at saves
  // its transcript without any of the events above reaching this tab (they
  // are filed as background activity). Refresh the list when such a save
  // lands, so the conversation appears -- or reorders -- without a reload.
  const prevBackgroundSavesRef = useRef(backgroundSaves)
  const runRefreshTimerRef = useRef(null)
  useEffect(() => {
    if (prevBackgroundSavesRef.current === backgroundSaves) return
    prevBackgroundSavesRef.current = backgroundSaves
    if (!chatHistoryEnabled || saveMode === 'none') return
    if (runRefreshTimerRef.current) clearTimeout(runRefreshTimerRef.current)
    history.refreshConversations(history.activeTag ? { tag: history.activeTag } : {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backgroundSaves, chatHistoryEnabled, saveMode])

  // Fallback for a run that ends without a save reaching this tab (it failed
  // before persisting, or another tab's socket got the frame). Delayed,
  // because a stopped run reports `cancelled` before its interrupted turn is
  // written, and fetching at that instant would miss the conversation.
  const prevTerminalRef = useRef('')
  useEffect(() => {
    if (!chatHistoryEnabled || saveMode === 'none') return
    const terminal = Object.values(runsByConversation || {})
      .filter(r => r && ['completed', 'failed', 'cancelled'].includes(r.status))
      .map(r => r.run_id)
      .sort()
      .join(',')
    const prev = prevTerminalRef.current
    prevTerminalRef.current = terminal
    if (prev !== terminal && terminal) {
      if (runRefreshTimerRef.current) clearTimeout(runRefreshTimerRef.current)
      runRefreshTimerRef.current = setTimeout(() => {
        runRefreshTimerRef.current = null
        history.refreshConversations(history.activeTag ? { tag: history.activeTag } : {})
      }, 2500)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runsByConversation, chatHistoryEnabled, saveMode])
  useEffect(() => () => {
    if (runRefreshTimerRef.current) clearTimeout(runRefreshTimerRef.current)
  }, [])

  // Reload the open conversation once the run it was opened under ends. The
  // stream the user joined partway is not replayed (issue #760); the stored
  // transcript is complete, so show that instead of a stale or partial view.
  // The refresh appends only the rows the view is missing when the stored
  // transcript still lines up with it, which keeps the user's scroll position
  // and their expanded tool rows (issue #959); a transcript that has diverged
  // from the view falls back to the full reload.
  useEffect(() => {
    if (!runEndedConversationId || runEndedConversationId !== activeConversationId) return
    let cancelled = false
    ;(async () => {
      const fullConv = await history.loadConversation(runEndedConversationId)
      if (cancelled) return
      applyRunEndRefresh({ fullConv, refreshJoinedConversation, loadSavedConversation })
      clearRunEndedConversation()
    })()
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runEndedConversationId, activeConversationId])

  // Immediately refresh when a conversation is saved (activeConversationId changes from null to a value)
  const prevActiveIdRef = useRef(activeConversationId)
  useEffect(() => {
    const prevId = prevActiveIdRef.current
    prevActiveIdRef.current = activeConversationId
    if (!prevId && activeConversationId && chatHistoryEnabled && saveMode !== 'none') {
      // Cancel any pending delayed refresh since we're fetching now
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      history.refreshConversations(history.activeTag ? { tag: history.activeTag } : {})
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConversationId, chatHistoryEnabled])

  // --- Resize logic (left-side panel: width = clientX - rect.left) ---
  const startResize = useCallback((e) => {
    setIsResizing(true)
    e.preventDefault()
  }, [])

  const stopResize = useCallback(() => {
    setIsResizing(false)
  }, [])

  const resize = useCallback((e) => {
    if (isResizing && panelRef.current) {
      const rect = panelRef.current.getBoundingClientRect()
      const newWidth = e.clientX - rect.left
      const clamped = Math.min(Math.max(newWidth, MIN_WIDTH), MAX_WIDTH)
      setSidebarWidth(clamped)
    }
  }, [isResizing, setSidebarWidth])

  useEffect(() => {
    if (!isResizing) {
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      return
    }
    const onMove = (e) => resize(e)
    const onUp = () => stopResize()
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [isResizing, resize, stopResize])

  // Clamp width if window shrinks
  useEffect(() => {
    const onWindowResize = () => {
      if (sidebarWidth > window.innerWidth * 0.5) {
        setSidebarWidth(Math.max(MIN_WIDTH, Math.floor(window.innerWidth * 0.4)))
      }
    }
    window.addEventListener('resize', onWindowResize)
    return () => window.removeEventListener('resize', onWindowResize)
  }, [sidebarWidth, setSidebarWidth])

  // --- Handlers ---
  const handleSearch = useCallback((e) => {
    const query = e.target.value
    setSearchText(query)
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => {
      history.searchConversations(query)
    }, 300)
  }, [history])

  // A tag is a different listing: the search on screen no longer describes
  // it. The text in the search box and the request it has not made yet go
  // with it, so the list is not replaced by a search for the old text.
  const handleFilterByTag = useCallback((tagName) => {
    if (searchTimerRef.current) {
      clearTimeout(searchTimerRef.current)
      searchTimerRef.current = null
    }
    setSearchText('')
    history.filterByTag(tagName)
  }, [history])

  // Every click on a row is the user's latest intent, and so is New Chat.
  // Opening a conversation awaits a fetch; without this, a slow fetch for an
  // earlier click (or one still out when New Chat was pressed) would resolve
  // last and yank the view back to a conversation the user already left --
  // in the parallel-runs flow, where users hop between running conversations,
  // that reads as the app switching on its own.
  // `newChatCount` covers New Chat pressed from a chat that has no id yet,
  // where the active id does not change.
  const loadSeqRef = useRef(0)
  useEffect(() => {
    if (!activeConversationId) loadSeqRef.current += 1
  }, [activeConversationId, newChatCount])

  const handleLoadConversation = useCallback(async (conv) => {
    if (conv._optimistic) return
    // Opening a nested conversation from search or a refresh must not leave it
    // hidden under a collapsed parent: expand only its ancestors, leaving every
    // other group exactly as the user left it.
    if (conv._ancestorIds?.length) {
      setCollapsedConversationIds(prev => prev.filter(id => !conv._ancestorIds.includes(id)))
    }
    // Don't reload the conversation we're already viewing. Clicking it is
    // still a choice to stay here, so it cancels an open under way.
    if (activeConversationId && conv.id === activeConversationId) {
      loadSeqRef.current += 1
      return
    }
    const level = { complianceEnabled: !!complianceEnabled, activeLevel: activeComplianceFilter, levels: complianceLevels }
    // Explain the refusal up front; the server applies the same rule on the
    // fetch, the restore and every turn whatever this check decides.
    const refusal = classificationRefusal(conv, level)
    if (refusal) {
      toast.error(refusal)
      return
    }
    // A refused or optimistic row cannot be opened, so clicking one leaves
    // an open already under way alone.
    const seq = ++loadSeqRef.current
    const fullConv = await history.loadConversation(conv.id, { complianceLevel: activeComplianceFilter ?? '' })
    if (seq !== loadSeqRef.current) return
    if (fullConv?.classificationRefused) {
      toast.error(fullConv.message)
      return
    }
    if (fullConv && !fullConv.error) {
      loadSavedConversation(fullConv)
      onMobileClose?.()
    }
  }, [history, loadSavedConversation, onMobileClose, activeConversationId, activeComplianceFilter, complianceEnabled, complianceLevels, toast, setCollapsedConversationIds])

  const handleToggleCollapsed = useCallback((e, conv) => {
    // The chevron is a view control, not a navigation control: stop the click
    // before the row's open handler sees it.
    e.stopPropagation()
    setCollapsedConversationIds(prev => (
      prev.includes(conv.id) ? prev.filter(id => id !== conv.id) : [...prev, conv.id]
    ))
  }, [setCollapsedConversationIds])

  // Walk back through older conversations (issue #1085). The rows already on
  // screen keep their place -- the next page is appended below them -- so
  // the scroll position and the open conversation are both untouched.
  const handleLoadMore = useCallback(() => {
    history.loadMore()
  }, [history])

  // The same rule the click applies, so a row that would be refused always
  // looks it -- with levels disabled too (a classified row cannot continue).
  const rowRefusal = conv => classificationRefusal(conv, {
    complianceEnabled: !!complianceEnabled, activeLevel: activeComplianceFilter, levels: complianceLevels,
  })

  const handleDeleteAll = useCallback(async () => {
    await history.deleteAll()
    setShowDeleteConfirm(null)
  }, [history])

  const handleContextMenu = useCallback((e, conv) => {
    if (conv._optimistic) return
    e.preventDefault()
    setContextMenu({ x: e.clientX, y: e.clientY, conversationId: conv.id })
  }, [])

  const handleDeleteConversation = useCallback(async () => {
    if (!contextMenu) return
    const { conversationId } = contextMenu
    const wasActive = activeConversationId === conversationId
    await history.deleteConversation(conversationId)
    setContextMenu(null)
    if (wasActive) clearChat({ skipConfirm: true })
  }, [contextMenu, activeConversationId, history, clearChat])

  const formatDate = (dateStr) => {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    const now = new Date()
    const diff = now - d
    if (diff < 60000) return 'just now'
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}d ago`
    return d.toLocaleDateString()
  }

  // --- Shared sidebar content ---
  const displayConversations = useMemo(
    () => (chatHistoryEnabled
      ? getDisplayConversations({
          conversations: history.conversations,
          messages,
          activeConversationId,
          chatHistoryEnabled,
          saveMode,
          runsByConversation,
        })
      : []),
    [
      chatHistoryEnabled,
      history.conversations,
      messages,
      activeConversationId,
      saveMode,
      runsByConversation,
    ]
  )

  // A search result or tag filter is the user's current frame of reference: a
  // group collapsed earlier must not hide a row that matches it, with no hint
  // why the list looks empty. While filtering, every group renders expanded.
  const filteringConversations = !!(history.searchQuery || history.activeTag)
  const collapsedConversationSet = new Set(
    filteringConversations ? [] : collapsedConversationIds
  )
  // `displayConversations` is already in tree order. Collapsing hides rows by
  // ancestor, not by deleting them: the hidden children keep their run state,
  // so expanding again is instant and a collapse can never stop an execution.
  const visibleConversations = displayConversations.filter(
    conv => !(conv._ancestorIds || []).some(id => collapsedConversationSet.has(id))
  )

  // Forget collapse state for rows that no longer exist, so the persisted set
  // cannot grow forever. Skip an in-flight or filtered list: its absent rows
  // are not deleted conversations. `loading` matters on the render right after
  // a filter is cleared -- the query is empty then, but the fetched list still
  // holds the previous filtered results until the unfiltered request lands.
  const visibleIdsKey = !filteringConversations && !history.loading
    ? displayConversations.map(conv => conv.id).join('\u0000')
    : ''
  useEffect(() => {
    if (!visibleIdsKey) return
    const present = new Set(visibleIdsKey.split('\u0000'))
    setCollapsedConversationIds(prev => {
      const next = prev.filter(id => present.has(id))
      return next.length === prev.length ? prev : next
    })
  }, [visibleIdsKey, setCollapsedConversationIds])

  // The open conversation must never be hidden by a collapsed group, however
  // it was opened (a row click, a reconnect, another tab). Expand only the
  // ancestors on its path; a no-op when they are already open.
  const activeAncestorKey = (displayConversations.find(
    conv => conv.id === activeConversationId
  )?._ancestorIds || []).join(',')
  useEffect(() => {
    if (!activeAncestorKey) return
    const ancestors = activeAncestorKey.split(',')
    setCollapsedConversationIds(prev => {
      const next = prev.filter(id => !ancestors.includes(id))
      return next.length === prev.length ? prev : next
    })
  }, [activeConversationId, activeAncestorKey, setCollapsedConversationIds])

  const sidebarContent = (
    <>
      {/* Header */}
      <div className="p-3 border-b border-gray-700 flex items-center justify-between flex-shrink-0">
        <h2 className="text-sm font-semibold text-gray-100">Conversations</h2>
        <div className="flex items-center gap-1">
          {/* Hide button - desktop only (mobile uses backdrop to close) */}
          <button
            onClick={() => {
              setIsCollapsed(true)
              onMobileClose?.()
            }}
            className="hidden md:block px-2 py-1 rounded text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-700 transition-colors"
            title="Hide sidebar"
          >
            Hide
          </button>
          {/* Close button - mobile only */}
          <button
            onClick={() => onMobileClose?.()}
            className="md:hidden p-1.5 rounded hover:bg-gray-700 transition-colors text-gray-400 hover:text-gray-200"
            title="Close"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {chatHistoryEnabled ? (
        <>
          {/* Search */}
          <div className="px-3 py-2 border-b border-gray-700 flex-shrink-0">
            <input
              type="text"
              placeholder="Search conversations..."
              value={searchText}
              onChange={handleSearch}
              className="w-full px-2 py-1.5 bg-gray-700 border border-gray-600 rounded text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Tag filter */}
          {history.tags.length > 0 && (
            <div className="px-3 py-2 border-b border-gray-700 flex flex-wrap gap-1 flex-shrink-0">
              <button
                onClick={() => handleFilterByTag(null)}
                className={`text-xs px-2 py-0.5 rounded ${!history.activeTag ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
              >
                All
              </button>
              {history.tags.map(tag => (
                <button
                  key={tag.id}
                  onClick={() => handleFilterByTag(tag.name)}
                  className={`text-xs px-2 py-0.5 rounded ${history.activeTag === tag.name ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
                >
                  {tag.name} ({tag.conversation_count})
                </button>
              ))}
            </div>
          )}

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto min-h-0">
            {history.loading && displayConversations.length === 0 ? (
              <div className="p-4 text-center text-gray-500 text-sm">Loading...</div>
            ) : displayConversations.length === 0 ? (
              <div className="p-4 text-center text-gray-500 text-sm">
                {history.searchQuery ? 'No matching conversations' : 'No saved conversations'}
              </div>
            ) : (
              <>
                <div className="py-1" role="list" aria-label="Conversations">
                  {visibleConversations.map(conv => {
                    const isGroupCollapsed = collapsedConversationSet.has(conv.id)
                    const ownRun = (runsByConversation || {})[conv.id]
                    // A collapsed parent summarizes its subtree, so a child that
                    // needs approval or failed is not invisible. Expanded rows
                    // show their own run only; the children are on screen.
                    const shownRun = conv._hasChildren && isGroupCollapsed
                      ? (conv._aggregateRun || ownRun)
                      : ownRun
                    const refusal = rowRefusal(conv)
                    return (
                      <ConversationTreeRow
                        key={conv.id}
                        conv={conv}
                        isActive={activeConversationId === conv.id}
                        isGroupCollapsed={isGroupCollapsed}
                        shownRun={shownRun}
                        refusal={refusal}
                        classification={classificationLabel(conv, complianceLevels)}
                        showClassification={!!(complianceEnabled || refusal)}
                        formatDate={formatDate}
                        onOpen={() => handleLoadConversation(conv)}
                        onContextMenu={(e) => handleContextMenu(e, conv)}
                        onToggleCollapsed={(e) => handleToggleCollapsed(e, conv)}
                      />
                    )
                  })}
                </div>
                {/* Older conversations (issue #1085): a control at the bottom of
                    the list that fetches the next page and appends it. A search
                    is its own list, so it has no page to walk back to. */}
                {!history.searchQuery && (
                  <ConversationLoadMore
                    hasMore={history.hasMore}
                    loading={history.loadingMore}
                    error={history.loadMoreError}
                    onLoadMore={handleLoadMore}
                  />
                )}
              </>
            )}
          </div>

          {/* Footer */}
          {displayConversations.length > 0 && (
            <div className="p-2 border-t border-gray-700 flex-shrink-0 flex flex-col gap-1">
              <button
                onClick={() => history.downloadAll()}
                className="w-full text-xs px-2 py-1.5 text-blue-400 hover:bg-blue-900/30 rounded transition-colors"
              >
                Download All Conversations
              </button>
              <button
                onClick={() => setShowDeleteConfirm('all')}
                className="w-full text-xs px-2 py-1.5 text-red-400 hover:bg-red-900/30 rounded transition-colors"
              >
                Delete All Conversations
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="flex-1 p-4 text-center text-gray-500 text-sm">
          Chat history is disabled.
          <br />
          <span className="text-xs text-gray-600 mt-1 block">
            Set FEATURE_CHAT_HISTORY_ENABLED=true to enable.
          </span>
        </div>
      )}

      {/* Context menu for individual conversation */}
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onDelete={handleDeleteConversation}
          onClose={() => setContextMenu(null)}
        />
      )}

      {/* Delete confirmation modal */}
      {showDeleteConfirm && (
        <div className="absolute inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowDeleteConfirm(null)}>
          <div className="bg-gray-800 border border-gray-600 rounded-lg p-4 m-4 max-w-sm" onClick={e => e.stopPropagation()}>
            <p className="text-gray-200 text-sm mb-4">
              Delete ALL conversations? This cannot be undone.
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowDeleteConfirm(null)}
                className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-300 rounded"
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteAll}
                className="px-3 py-1.5 text-sm bg-red-600 hover:bg-red-500 text-white rounded"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )

  // --- Desktop: collapsed view (skip if mobile overlay is open) ---
  if (isCollapsed && !mobileOpen) {
    return (
      <div className="hidden md:flex w-10 bg-gray-800 border-r border-gray-700 flex-col items-center pt-2 flex-shrink-0">
        <button
          onClick={() => setIsCollapsed(false)}
          className="p-1.5 rounded hover:bg-gray-700 transition-colors text-gray-400 hover:text-gray-200"
          title="Show conversations"
        >
          <svg xmlns="http://www.w3.org/2000/svg" className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7M6 5l7 7-7 7" />
          </svg>
        </button>
      </div>
    )
  }

  return (
    <>
      {/* Mobile overlay: slide-in from left */}
      {mobileOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={() => onMobileClose?.()}
        />
      )}
      <aside
        ref={panelRef}
        className={`
          bg-gray-800 border-r border-gray-700 flex flex-col h-full flex-shrink-0
          fixed md:relative inset-y-0 left-0 z-50 md:z-auto
          transition-transform duration-200 ease-in-out md:transition-none md:translate-x-0
          ${mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0'}
        `}
        style={{ width: `${sidebarWidth}px`, maxWidth: '85vw' }}
      >
        {sidebarContent}

        {/* Resize handle on right edge (desktop only) */}
        <div
          className="hidden md:block absolute right-0 top-0 w-1.5 h-full cursor-col-resize bg-transparent hover:bg-blue-500/50 transition-colors group"
          onMouseDown={startResize}
          style={{ transform: 'translateX(50%)' }}
        >
          <div className="absolute right-0 top-1/2 -translate-y-1/2 w-1 h-12 bg-gray-600 group-hover:bg-blue-500 transition-colors rounded-sm" />
        </div>
      </aside>
    </>
  )
}

export default Sidebar
