import { useState, useEffect, useCallback, useRef } from 'react'
import { useChat } from '../contexts/ChatContext'
import { useConversationHistory } from '../hooks/useConversationHistory'

const Sidebar = () => {
  const {
    clearChat, features, isIncognito, setIsIncognito,
    activeConversationId, loadSavedConversation,
  } = useChat()

  const chatHistoryEnabled = features?.chat_history
  const [isCollapsed, setIsCollapsed] = useState(false)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(null) // 'selected' | 'all' | null
  const searchTimerRef = useRef(null)

  const history = useConversationHistory()

  // Fetch conversations on mount and when feature is enabled
  useEffect(() => {
    if (chatHistoryEnabled) {
      history.fetchConversations()
      history.fetchTags()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatHistoryEnabled])

  const handleSearch = useCallback((e) => {
    const query = e.target.value
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current)
    searchTimerRef.current = setTimeout(() => {
      history.searchConversations(query)
    }, 300)
  }, [history])

  const handleLoadConversation = useCallback(async (conv) => {
    const fullConv = await history.loadConversation(conv.id)
    if (fullConv && !fullConv.error) {
      loadSavedConversation(fullConv)
    }
  }, [history, loadSavedConversation])

  const toggleSelect = useCallback((id, e) => {
    e.stopPropagation()
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const handleDeleteSelected = useCallback(async () => {
    if (selectedIds.size === 0) return
    await history.deleteMultiple([...selectedIds])
    setSelectedIds(new Set())
    setShowDeleteConfirm(null)
  }, [selectedIds, history])

  const handleDeleteAll = useCallback(async () => {
    await history.deleteAll()
    setSelectedIds(new Set())
    setShowDeleteConfirm(null)
  }, [history])

  const handleNewConversation = useCallback(() => {
    clearChat()
    setSelectedIds(new Set())
  }, [clearChat])

  const refreshList = useCallback(() => {
    if (history.activeTag) {
      history.filterByTag(history.activeTag)
    } else if (history.searchQuery) {
      history.searchConversations(history.searchQuery)
    } else {
      history.fetchConversations()
    }
  }, [history])

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

  if (isCollapsed) {
    return (
      <div className="w-12 bg-gray-800 border-r border-gray-700 p-2 flex flex-col gap-2">
        <button
          onClick={() => setIsCollapsed(false)}
          className="w-full p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-300"
          title="Expand sidebar"
        >
          &gt;
        </button>
        {isIncognito && (
          <div
            className="w-full p-1 rounded bg-red-900 text-red-200 text-xs text-center"
            title="Incognito mode active"
          >
            IC
          </div>
        )}
      </div>
    )
  }

  return (
    <aside className="w-64 bg-gray-800 border-r border-gray-700 flex flex-col h-full">
      {/* Header */}
      <div className="p-3 border-b border-gray-700 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-100">Conversations</h2>
        <div className="flex items-center gap-1">
          <button
            onClick={refreshList}
            className="p-1.5 rounded hover:bg-gray-700 transition-colors text-gray-400 hover:text-gray-200"
            title="Refresh"
          >
            <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <button
            onClick={() => setIsCollapsed(true)}
            className="p-1.5 rounded hover:bg-gray-700 transition-colors text-gray-400 hover:text-gray-200"
            title="Collapse sidebar"
          >
            &lt;
          </button>
        </div>
      </div>

      {/* Incognito Toggle */}
      <div className={`px-3 py-2 border-b ${isIncognito ? 'bg-red-950 border-red-800' : 'border-gray-700'}`}>
        <label className="flex items-center gap-2 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={isIncognito}
            onChange={(e) => setIsIncognito(e.target.checked)}
            className="rounded border-gray-600 bg-gray-700 text-red-500 focus:ring-red-500"
          />
          <span className={`text-xs font-medium ${isIncognito ? 'text-red-300' : 'text-gray-400'}`}>
            Incognito Mode
          </span>
          {isIncognito && (
            <span className="text-xs text-red-400 ml-auto">Not saving</span>
          )}
        </label>
      </div>

      {/* New Conversation Button */}
      <div className="p-3 border-b border-gray-700">
        <button
          onClick={handleNewConversation}
          className="w-full px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg transition-colors"
        >
          + New Conversation
        </button>
      </div>

      {/* Only show list/search when chat history is enabled */}
      {chatHistoryEnabled ? (
        <>
          {/* Search */}
          <div className="px-3 py-2 border-b border-gray-700">
            <input
              type="text"
              placeholder="Search conversations..."
              onChange={handleSearch}
              className="w-full px-2 py-1.5 bg-gray-700 border border-gray-600 rounded text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>

          {/* Tag filter */}
          {history.tags.length > 0 && (
            <div className="px-3 py-2 border-b border-gray-700 flex flex-wrap gap-1">
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

          {/* Bulk actions */}
          {selectedIds.size > 0 && (
            <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-2">
              <span className="text-xs text-gray-400">{selectedIds.size} selected</span>
              <button
                onClick={() => setShowDeleteConfirm('selected')}
                className="text-xs px-2 py-1 bg-red-700 hover:bg-red-600 text-white rounded"
              >
                Delete
              </button>
              <button
                onClick={() => setSelectedIds(new Set())}
                className="text-xs px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded ml-auto"
              >
                Clear
              </button>
            </div>
          )}

          {/* Conversation list */}
          <div className="flex-1 overflow-y-auto">
            {history.loading ? (
              <div className="p-4 text-center text-gray-500 text-sm">Loading...</div>
            ) : history.conversations.length === 0 ? (
              <div className="p-4 text-center text-gray-500 text-sm">
                {history.searchQuery ? 'No matching conversations' : 'No saved conversations'}
              </div>
            ) : (
              <div className="py-1">
                {history.conversations.map(conv => (
                  <div
                    key={conv.id}
                    onClick={() => handleLoadConversation(conv)}
                    className={`px-3 py-2 cursor-pointer border-l-2 transition-colors ${
                      activeConversationId === conv.id
                        ? 'bg-gray-700 border-blue-500'
                        : 'border-transparent hover:bg-gray-750 hover:border-gray-600'
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        checked={selectedIds.has(conv.id)}
                        onChange={(e) => toggleSelect(conv.id, e)}
                        className="mt-1 rounded border-gray-600 bg-gray-700 text-blue-500 focus:ring-blue-500"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="text-sm text-gray-200 truncate" title={conv.title}>
                          {conv.title || 'Untitled'}
                        </div>
                        {conv.preview && (
                          <div className="text-xs text-gray-500 truncate mt-0.5">
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
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Footer actions */}
          {history.conversations.length > 0 && (
            <div className="p-2 border-t border-gray-700">
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

      {/* Delete confirmation modal */}
      {showDeleteConfirm && (
        <div className="absolute inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowDeleteConfirm(null)}>
          <div className="bg-gray-800 border border-gray-600 rounded-lg p-4 m-4 max-w-sm" onClick={e => e.stopPropagation()}>
            <p className="text-gray-200 text-sm mb-4">
              {showDeleteConfirm === 'all'
                ? 'Delete ALL conversations? This cannot be undone.'
                : `Delete ${selectedIds.size} selected conversation(s)? This cannot be undone.`
              }
            </p>
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setShowDeleteConfirm(null)}
                className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-300 rounded"
              >
                Cancel
              </button>
              <button
                onClick={showDeleteConfirm === 'all' ? handleDeleteAll : handleDeleteSelected}
                className="px-3 py-1.5 text-sm bg-red-600 hover:bg-red-500 text-white rounded"
              >
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}

export default Sidebar
