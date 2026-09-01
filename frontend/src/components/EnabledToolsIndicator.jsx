import { useChat } from '../contexts/ChatContext'
import { X, ChevronDown, ChevronUp, Wrench } from 'lucide-react'
import { useState } from 'react'
import { getMcpNameFromKey } from '../utils/mcpKeys'

const EnabledToolsIndicator = () => {
  const { selectedTools, toggleTool, settings, updateSettings, tools = [] } = useChat()
  const [isExpanded, setIsExpanded] = useState(false)
  const autoApproveOn = Boolean(settings?.autoApproveTools)

  const allTools = Array.from(selectedTools).map(key => {
    return { name: getMcpNameFromKey(key, tools), key, type: 'tool' }
  })

  // Only show tools (prompts are now in the PromptSelector)
  if (allTools.length === 0) return null

  // Threshold for showing compact view
  const COMPACT_THRESHOLD = 5
  const shouldShowCompact = allTools.length > COMPACT_THRESHOLD
  // Removing tools can drop the count back under the threshold while
  // isExpanded is still true, which takes the "Show less" button away with it
  // (review on #876). Derive the layout from both, so the strip falls back to
  // the collapsed single row rather than getting stuck wrapped with no control
  // that can collapse it.
  const isExpandedLayout = shouldShowCompact && isExpanded
  const displayTools = shouldShowCompact && !isExpanded 
    ? allTools.slice(0, COMPACT_THRESHOLD) 
    : allTools

  // Collapsed, the strip is a single row on every viewport (#870): the pill
  // row never wraps — it scrolls horizontally instead, like the follow-up
  // suggestions. Expanded, it wraps into a capped, vertically scrolling block
  // (#876) — with nowrap the extra pills only lengthened a row that already
  // scrolled, so "+N more" looked like it did nothing. The "+N more" toggle
  // and the auto-approve toggle sit outside the scroll region so they stay
  // reachable even when the pills overflow (review).
  return (
    <div className={`flex gap-2 text-xs text-gray-400 mb-2 ${isExpandedLayout ? 'items-start' : 'items-center'}`}>
      <span className={`flex-shrink-0 flex items-center ${isExpandedLayout ? 'mt-1' : ''}`} aria-label="Active Tools">
        <Wrench className="w-3.5 h-3.5 sm:hidden" aria-hidden="true" />
        <span className="hidden sm:inline">Active Tools:</span>
      </span>
      <div
        className={`flex-1 min-w-0 flex items-center gap-1 scrollbar-hide ${
          isExpandedLayout
            ? 'flex-wrap max-h-24 overflow-y-auto'
            : 'flex-nowrap overflow-x-auto'
        }`}
        tabIndex={0}
        aria-label={
          isExpandedLayout
            ? 'Selected tools; scroll vertically to see more'
            : 'Selected tools; scroll horizontally to see more'
        }
      >
        {displayTools.map((item, idx) => (
          <div
            key={idx}
            className="flex-shrink-0 px-2 py-1 rounded flex items-center gap-1 bg-gray-700 text-gray-300"
          >
            <span className="max-w-40 truncate" title={item.name}>{item.name}</span>
            <button
              onClick={() => toggleTool(item.key)}
              className="hover:bg-red-600 hover:bg-opacity-50 rounded p-0.5 transition-colors flex-shrink-0"
              title={`Remove ${item.name}`}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
      </div>
      {shouldShowCompact && (
        <button
          type="button"
          onClick={() => setIsExpanded(!isExpanded)}
          aria-expanded={isExpanded}
          className={`flex-shrink-0 px-2 py-1 rounded flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-gray-300 transition-colors ${
            isExpandedLayout ? 'mt-0.5' : ''
          }`}
          title={isExpanded ? 'Show less' : `Show ${allTools.length - COMPACT_THRESHOLD} more`}
        >
          {isExpanded ? (
            <>
              <ChevronUp className="w-3 h-3" />
              <span>Show less</span>
            </>
          ) : (
            <>
              <span>+{allTools.length - COMPACT_THRESHOLD} more</span>
              <ChevronDown className="w-3 h-3" />
            </>
          )}
        </button>
      )}
      {/* Auto-approve lives here rather than on every approval row (#762).
          This strip is the persistent per-conversation status bar, so one
          indicator replaces one pill per tool call — and it stays on screen
          instead of scrolling away with the transcript. */}
      <button
        type="button"
        onClick={() => {
          try {
            updateSettings?.({ autoApproveTools: !autoApproveOn })
          } catch (e) {
            console.error('Failed to toggle auto-approve from the Active Tools strip', e)
          }
        }}
        aria-pressed={autoApproveOn}
        className={`flex-shrink-0 px-2 py-1 rounded border transition-colors cursor-pointer ${
          isExpandedLayout ? 'mt-0.5' : ''
        } ${
          autoApproveOn
            ? 'bg-blue-600 text-white border-blue-500 hover:bg-blue-700'
            : 'bg-gray-700 text-gray-300 border-gray-600 hover:bg-gray-600'
        }`}
        title="Click to toggle auto-approve for non-admin tool calls. Admin-required calls will still prompt."
      >
        {autoApproveOn ? 'Auto-approve ON' : 'Auto-approve OFF'}
      </button>
    </div>
  )
}

export default EnabledToolsIndicator