import { useState, useEffect, useCallback, useRef } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useConversationHistory } from '../hooks/useConversationHistory'
import { usePersistentState } from '../hooks/chat/usePersistentState'
import { getDisplayConversations } from '../utils/getDisplayConversations'

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
    features, activeConversationId, loadSavedConversation, messages, isIncognito, clearChat,
  } = useChat()

  const chatHistoryEnabled = features?.chat_history
  const [sidebarWidth, setSidebarWidth] = usePersistentState('chatui-sidebar-width', DEFAULT_WIDTH)
  const [isCollapsed, setIsCollapsed] = usePersistentState('chatui-sidebar-collapsed', false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(null)
  const [contextMenu, setContextMenu] = useState(null)
  const [isResizing, setIsResizing] = useState(false)
  const searchTimerRef = useRef(null)
  const prevMessageCountRef = useRef(0)
  const refreshTimerRef = useRef(null)
  const panelRef = useRef(null)

  const history = useConversationHistory()

  // Fetch conversations on mount and when feature is enabled
  useEffect(() => {
    if (chatHistoryEnabled) {
      history.fetchConversations()
      history.fetchTags()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatHistoryEnabled])

  // Auto-refresh conversation list when messages change
  useEffect(() => {
    if (!chatHistoryEnabled || isIncognito) return
    const currentCount = messages?.length || 0
    const prevCount = prevMessageCountRef.current
    prevMessageCountRef.current = currentCount

    if (currentCount > prevCount && currentCount > 0) {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = setTimeout(() => {
        history.fetchConversations(history.activeTag ? { tag: history.activeTag } : {})
      }, 1500)
    }

    if (currentCount === 0 && prevCount > 0) {
      history.fetchConversations(history.activeTag ? { tag: history.activeTag } : {})
    }

    return () => {
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages?.length, chatHistoryEnabled])

  // Immediately refresh when a conversation is saved (activeConversationId changes from null to a value)
  const prevActiveIdRef = useRef(activeConversationId)
  useEffect(() => {
    const prevId = prevActiveIdRef.current
    prevActiveIdRef.current = activeConversationId
    if (!prevId && activeConversationId && chatHistoryEnabled && !isIncognito) {
      // Cancel any pending delayed refresh since we're fetching now
      if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
      history.fetchConversations(history.activeTag ? { tag: history.activeTag } : {})
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
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => {
      history.searchConversations(query)
    }, 300)
  }, [history])

  const handleLoadConversation = useCallback(async (conv) => {
    if (conv._optimistic) return
    // Don't reload the conversation we're already viewing
    if (activeConversationId && conv.id === activeConversationId) return
    const fullConv = await history.loadConversation(conv.id)
    if (fullConv && !fullConv.error) {
      loadSavedConversation(fullConv)
      onMobileClose?.()
    }
  }, [history, loadSavedConversation, onMobileClose, activeConversationId])

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
    if (wasActive) clearChat()
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
  const displayConversations = chatHistoryEnabled
    ? getDisplayConversations({
        conversations: history.conversations,
        messages,
        activeConversationId,
        chatHistoryEnabled,
        isIncognito,
      })
    : []

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
              onChange={handleSearch}
              className="w-full px-2 py-1.5 bg-gray-700 border border-gray-600 rounded text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Tag filter */}
          {history.tags.length > 0 && (
            <div className="px-3 py-2 border-b border-gray-700 flex flex-wrap gap-1 flex-shrink-0">
              <button
                onClick={() => history.filterByTag(null)}
                className={`text-xs px-2 py-0.5 rounded ${!history.activeTag ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-400 hover:bg-gray-600'}`}
              >
                All
              </button>
              {history.tags.map(tag => (
                <button
                  key={tag.id}
                  onClick={() => history.filterByTag(tag.name)}
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
              <div className="py-1">
                {displayConversations.map(conv => (
                  <div
                    key={conv.id}
                    onClick={() => handleLoadConversation(conv)}
                    onContextMenu={(e) => handleContextMenu(e, conv)}
                    className={`px-3 py-2 cursor-pointer border-l-2 border-b border-b-gray-700/50 transition-colors ${
                      conv._optimistic
                        ? 'bg-gray-750 border-l-blue-400 opacity-80'
                        : activeConversationId === conv.id
                          ? 'bg-gray-700 border-l-blue-500'
                          : 'border-l-transparent hover:bg-gray-750 hover:border-l-gray-600'
                    }`}
                  >
                    <div className="min-w-0 overflow-hidden">
                      <div className="text-sm text-gray-200 truncate" title={conv.title}>
                        {conv.title || 'Untitled'}
                      </div>
                      {conv.preview && (
                        <div className={`text-xs truncate mt-0.5 ${conv._optimistic ? 'text-blue-400' : 'text-gray-500'}`}>
                          {conv.preview}
                        </div>
                      )}
                      <div className="flex items-center gap-2 mt-1">
                        <span className="text-xs text-gray-600">{formatDate(conv.updated_at)}</span>
                        <span className="text-xs text-gray-600">{conv.message_count} msgs</span>
                      </div>
                      {conv.tags && conv.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1 mt-1">
                          {conv.tags.map(tag => (
                            <span key={tag} className="text-xs bg-gray-700 text-gray-400 px-1.5 py-0.5 rounded">
                              {tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
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
