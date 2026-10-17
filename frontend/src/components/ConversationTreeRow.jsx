// One row of the conversation history tree (issue #1081).
//
// Kept separate from Sidebar so the tree-specific markup -- indentation,
// chevron, child count, branch connector and the folded status marker -- does
// not grow the sidebar's already large component. Every row is still one
// conversation: navigation, context menu and active styling are the same for a
// root and for a grandchild.

const RUN_INDICATORS = {
  queued: { label: 'Queued', dot: 'bg-blue-400', text: 'text-blue-400', pulse: true },
  running: { label: 'Running', dot: 'bg-green-400', text: 'text-green-400', pulse: true },
  waiting_for_input: { label: 'Needs approval', dot: 'bg-amber-400', text: 'text-amber-400', pulse: true },
  failed: { label: 'Failed', dot: 'bg-red-400', text: 'text-red-400', pulse: false },
  cancelled: { label: 'Stopped', dot: 'bg-gray-500', text: 'text-gray-500', pulse: false },
}

export function RunIndicator({ run, count = 0 }) {
  // `completed` is deliberately absent: a finished run is just a conversation
  // with new messages in it, and a permanent green tick on every row it ever
  // touched would be noise.
  const style = run && RUN_INDICATORS[run.status]
  if (!style) return null
  // A collapsed parent folds its subtree's runs into one marker. The count
  // says how many conversations are in that state, not how many runs -- a
  // conversation has at most one non-terminal run.
  const label = count > 1 ? `${style.label} (${count})` : style.label
  return (
    <span className={`flex items-center gap-1 text-xs ${style.text}`} title={run.error || label}>
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot} ${style.pulse ? 'animate-pulse' : ''}`} />
      {label}
    </span>
  )
}

export function ConversationTreeRow({
  conv,
  isActive,
  isGroupCollapsed,
  shownRun,
  refusal,
  classification,
  showClassification,
  formatDate,
  onOpen,
  onContextMenu,
  onToggleCollapsed,
}) {
  const childLabel = `${conv._childCount} sub-conversation${conv._childCount === 1 ? '' : 's'}`
  return (
    <div
      role="treeitem"
      aria-level={conv._depth + 1}
      aria-expanded={conv._hasChildren ? !isGroupCollapsed : undefined}
      onClick={onOpen}
      onContextMenu={onContextMenu}
      title={refusal || undefined}
      style={{ paddingLeft: `${12 + conv._depth * 14}px` }}
      className={`py-2 pr-3 cursor-pointer border-l-2 border-b border-b-gray-700/50 transition-colors ${
        refusal ? 'opacity-50 ' : ''
      }${
        conv._optimistic
          ? 'bg-gray-750 border-l-blue-400 opacity-80'
          : isActive
            ? 'bg-gray-700 border-l-blue-500'
            : 'border-l-transparent hover:bg-gray-750 hover:border-l-gray-600'
      }`}
    >
      <div className="min-w-0 overflow-hidden">
        <div className="flex items-center gap-1 min-w-0">
          {conv._hasChildren ? (
            <button
              onClick={onToggleCollapsed}
              aria-label={isGroupCollapsed ? 'Expand sub-conversations' : 'Collapse sub-conversations'}
              aria-expanded={!isGroupCollapsed}
              title={isGroupCollapsed ? `Show ${childLabel}` : 'Hide sub-conversations'}
              className="p-0.5 -ml-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-700 flex-shrink-0"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                className={`h-3.5 w-3.5 transition-transform ${isGroupCollapsed ? '' : 'rotate-90'}`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="m9 5 7 7-7 7" />
              </svg>
            </button>
          ) : conv._depth > 0 ? (
            <svg
              aria-hidden="true"
              xmlns="http://www.w3.org/2000/svg"
              className="h-3 w-3 text-gray-600 flex-shrink-0"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v9a3 3 0 0 0 3 3h6m-4-4 4 4-4 4" />
            </svg>
          ) : null}
          <div className="text-sm text-gray-200 truncate" title={conv.title}>
            {conv.title || 'Untitled'}
          </div>
          {conv._hasChildren && (
            <span
              className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded flex-shrink-0"
              data-testid="conversation-child-count"
              title={childLabel}
            >
              {conv._childCount}
            </span>
          )}
        </div>
        {conv.preview && (
          <div className={`text-xs truncate mt-0.5 ${conv._optimistic ? 'text-blue-400' : 'text-gray-500'}`}>
            {conv.preview}
          </div>
        )}
        <div className="flex items-center gap-2 mt-1">
          <span className="text-xs text-gray-600">{formatDate(conv.updated_at)}</span>
          <span className="text-xs text-gray-600">{conv.message_count} msgs</span>
          {showClassification && classification && (
            <span
              className="text-xs bg-gray-700 text-gray-300 px-1.5 py-0.5 rounded"
              data-testid="conversation-classification"
            >
              {classification}
            </span>
          )}
          <RunIndicator run={shownRun} count={conv._hasChildren && isGroupCollapsed ? shownRun?.count : 0} />
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
  )
}
