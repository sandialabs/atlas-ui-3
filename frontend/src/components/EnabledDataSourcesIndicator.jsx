import { useMemo, useState } from 'react'
import { Database, X, ChevronDown, ChevronUp } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { openSettingsPanel } from '../utils/settingsPanelEvents'

// Above this many datasets the strip collapses to a summary pill rather than
// wrapping into a wall of names (issue #839 review).
const COMPACT_THRESHOLD = 3

/**
 * Enabled data sources, shown as pills right above the message box.
 *
 * The reviewer asked to see which datasets are on without leaving the chat
 * bar: a small count expands to the individual pills, each removable, and the
 * whole strip is a shortcut into the sources picker inside Tools and Settings.
 */
const EnabledDataSourcesIndicator = () => {
  const { ragSources = [], selectedDataSources, toggleDataSource, features } = useChat()
  const [isExpanded, setIsExpanded] = useState(false)

  const selected = useMemo(() => {
    const keys = selectedDataSources ? Array.from(selectedDataSources) : []
    return keys.map(key => {
      const match = ragSources.find(s => `${s.serverName}:${s.id}` === key)
      return { key, label: match?.label || match?.name || key.split(':').slice(1).join(':') || key }
    })
  }, [selectedDataSources, ragSources])

  if (!features?.rag || selected.length === 0) return null

  const showCompact = selected.length > COMPACT_THRESHOLD && !isExpanded
  const shown = showCompact ? selected.slice(0, COMPACT_THRESHOLD) : selected

  return (
    <div className="flex items-start gap-2 text-xs text-gray-400 mb-2" data-testid="active-data-sources">
      <button
        type="button"
        onClick={() => openSettingsPanel({ tab: 'tools' })}
        className="mt-1 flex-shrink-0 flex items-center gap-1 hover:text-blue-400 transition-colors"
        title="Open the data sources picker in Tools and Settings"
      >
        <Database className="w-3 h-3" />
        <span>Data Sources:</span>
      </button>
      <div className="flex-1 min-w-0 flex flex-wrap gap-1 items-center">
        {shown.map(item => (
          <span
            key={item.key}
            className="px-2 py-1 rounded-full flex items-center gap-1 bg-blue-900/40 border border-blue-700 text-blue-200 max-w-full min-w-0"
          >
            <span className="min-w-0 truncate" title={item.label}>{item.label}</span>
            <button
              type="button"
              onClick={() => toggleDataSource(item.key)}
              className="hover:bg-red-600 hover:bg-opacity-50 rounded p-0.5 transition-colors flex-shrink-0"
              title={`Remove ${item.label}`}
              aria-label={`Remove ${item.label}`}
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
        {selected.length > COMPACT_THRESHOLD && (
          <button
            type="button"
            onClick={() => setIsExpanded(!isExpanded)}
            className="px-2 py-1 rounded-full flex items-center gap-1 bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
            title={isExpanded ? 'Show less' : `Show ${selected.length - COMPACT_THRESHOLD} more`}
          >
            {isExpanded ? (
              <>
                <ChevronUp className="w-3 h-3" />
                <span>Show less</span>
              </>
            ) : (
              <>
                <span>+{selected.length - COMPACT_THRESHOLD} more</span>
                <ChevronDown className="w-3 h-3" />
              </>
            )}
          </button>
        )}
      </div>
    </div>
  )
}

export default EnabledDataSourcesIndicator
