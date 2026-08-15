import { useChat } from '../contexts/ChatContext'
import { X, ChevronDown, ChevronUp } from 'lucide-react'
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
  const displayTools = shouldShowCompact && !isExpanded 
    ? allTools.slice(0, COMPACT_THRESHOLD) 
    : allTools

  return (
    <div className="flex items-start gap-2 text-xs text-gray-400 mb-2">
      <span className="mt-1 flex-shrink-0">Active Tools:</span>
      <div className="flex-1 min-w-0 flex flex-wrap gap-1 items-center">
        {displayTools.map((item, idx) => (
          <div
            key={idx}
            className="px-2 py-1 rounded flex items-center gap-1 bg-gray-700 text-gray-300 max-w-full min-w-0"
          >
            <span className="min-w-0 truncate" title={item.name}>{item.name}</span>
            <button
              onClick={() => toggleTool(item.key)}
              className="hover:bg-red-600 hover:bg-opacity-50 rounded p-0.5 transition-colors flex-shrink-0"
              title={`Remove ${item.name}`}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
        {shouldShowCompact && (
          <button
            onClick={() => setIsExpanded(!isExpanded)}
            className="px-2 py-1 rounded flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-gray-300 transition-colors"
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
          className={`px-2 py-1 rounded border transition-colors cursor-pointer ${
            autoApproveOn
              ? 'bg-blue-600 text-white border-blue-500 hover:bg-blue-700'
              : 'bg-gray-700 text-gray-300 border-gray-600 hover:bg-gray-600'
          }`}
          title="Click to toggle auto-approve for non-admin tool calls. Admin-required calls will still prompt."
        >
          {autoApproveOn ? 'Auto-approve ON' : 'Auto-approve OFF'}
        </button>
      </div>
    </div>
  )
}

export default EnabledToolsIndicator