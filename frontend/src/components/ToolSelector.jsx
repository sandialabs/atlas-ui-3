import { useCallback, useMemo, useState, useRef, useEffect } from 'react'
import { Wrench, ChevronDown, Search, SlidersHorizontal, Server } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useOptionalMarketplace } from '../contexts/MarketplaceContext'
import { openSettingsPanel } from '../utils/settingsPanelEvents'
import { useEscapeKey } from '../hooks/useEscapeKey'

// Descriptions are the point of this menu -- the reviewer's complaint was a
// "long, indistinguishable list of tool names" -- but a full paragraph in a
// popover is unreadable, so each row gets a one-line summary.
const SUMMARY_LIMIT = 90

const summarize = (text) => {
  if (!text) return ''
  const oneLine = String(text).replace(/\s+/g, ' ').trim()
  return oneLine.length > SUMMARY_LIMIT ? `${oneLine.slice(0, SUMMARY_LIMIT - 1)}...` : oneLine
}

/**
 * Chat-bar tool picker (issue #839 review).
 *
 * Mirrors PromptSelector: a small control under the message box that opens a
 * scrollable menu of every available tool as an icon + name + one-line
 * description with an on/off toggle, so small changes never need a trip into
 * the full Tools and Settings panel. The footer opens that panel for
 * everything else.
 */
const ToolSelector = () => {
  const { tools: allTools = [], selectedTools, toggleTool, features, complianceLevelFilter } = useChat()
  // Optional, like ModelSelector's: the chat bar renders in test harnesses and
  // embeds that do not mount MarketplaceProvider.
  const marketplace = useOptionalMarketplace()

  // Same source of rows as the Tools and Settings panel (ToolsPanel.jsx): a
  // tool the panel hides -- because its server is unselected in the
  // marketplace, or because it is above the active compliance level -- must not
  // be listable or toggleable from the chat bar either. With no marketplace to
  // filter against, fall back to the unfiltered list rather than showing none.
  const complianceEnabled = features?.compliance_levels
  const tools = useMemo(() => {
    if (!marketplace) return allTools
    return complianceEnabled
      ? marketplace.getComplianceFilteredTools(complianceLevelFilter)
      : marketplace.getFilteredTools()
  }, [marketplace, complianceEnabled, complianceLevelFilter, allTools])
  const [isOpen, setIsOpen] = useState(false)
  const [query, setQuery] = useState('')
  const dropdownRef = useRef(null)
  const triggerRef = useRef(null)

  // Escape closes the menu and puts focus back on the button that opened it,
  // so it is not lost to the end of the document (PR #839 review). Outside
  // mousedown alone left keyboard users with no way out.
  const closeAndRestoreFocus = useCallback(() => {
    setIsOpen(false)
    triggerRef.current?.focus()
  }, [])
  useEscapeKey(isOpen, closeAndRestoreFocus)

  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) setIsOpen(false)
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase()
    return (tools || [])
      .map(server => {
        const detailed = server.tools_detailed || []
        const rows = (server.tools || []).map(name => ({
          name,
          key: `${server.server}_${name}`,
          description: summarize(detailed.find(t => t.name === name)?.description),
        })).filter(row => !needle
          || row.name.toLowerCase().includes(needle)
          || row.description.toLowerCase().includes(needle)
          || String(server.server).toLowerCase().includes(needle))
        return { server: server.server, rows }
      })
      .filter(group => group.rows.length > 0)
  }, [tools, query])

  if (!features?.tools) return null

  const selectedCount = selectedTools?.size || 0

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        ref={triggerRef}
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-blue-400 transition-colors"
        title="Turn tools on and off"
        aria-expanded={isOpen}
        aria-haspopup="true"
      >
        <Wrench className="w-3 h-3" />
        <span className="underline decoration-dotted">
          {selectedCount > 0 ? `${selectedCount} tool${selectedCount === 1 ? '' : 's'}` : 'Tools'}
        </span>
        <ChevronDown className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute bottom-full left-0 mb-1 w-96 max-w-[90vw] bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-96 overflow-y-auto z-50">
          <div className="p-2 border-b border-gray-700 sticky top-0 bg-gray-800">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                type="text"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search tools..."
                className="w-full pl-7 pr-2 py-1.5 bg-gray-700 border border-gray-600 rounded text-xs text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>

          {groups.length === 0 ? (
            <div className="px-3 py-6 text-center text-xs text-gray-400">No tools match that search</div>
          ) : groups.map(group => (
            <div key={group.server}>
              <div className="px-3 py-1.5 bg-gray-750 border-b border-gray-700 flex items-center gap-2 text-xs font-semibold text-gray-300 capitalize">
                <Server className="w-3 h-3 text-gray-400" />
                {group.server}
              </div>
              {group.rows.map(row => {
                const isOn = selectedTools?.has(row.key)
                return (
                  <button
                    key={row.key}
                    type="button"
                    onClick={() => toggleTool(row.key)}
                    aria-pressed={isOn}
                    className={`w-full px-3 py-2 text-left flex items-start gap-2 border-b border-gray-700 last:border-b-0 transition-colors ${
                      isOn ? 'bg-blue-900/30 hover:bg-blue-900/40' : 'hover:bg-gray-700'
                    }`}
                  >
                    <Wrench className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${isOn ? 'text-blue-400' : 'text-gray-500'}`} />
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-gray-200 break-words">{row.name}</span>
                      {row.description && (
                        <span className="block text-xs text-gray-400 break-words">{row.description}</span>
                      )}
                    </span>
                    {/* Toggle, not a checkbox: the reviewer asked for tools that
                        read as on/off at a glance. */}
                    <span
                      className={`mt-0.5 w-8 h-4 rounded-full flex-shrink-0 relative transition-colors ${
                        isOn ? 'bg-blue-600' : 'bg-gray-600'
                      }`}
                    >
                      <span className={`absolute top-0.5 w-3 h-3 bg-white rounded-full transition-all ${isOn ? 'left-4' : 'left-0.5'}`} />
                    </span>
                  </button>
                )
              })}
            </div>
          ))}

          <button
            type="button"
            onClick={() => { setIsOpen(false); openSettingsPanel({ tab: 'tools' }) }}
            className="w-full px-3 py-2 flex items-center gap-2 text-xs text-gray-300 hover:bg-gray-700 border-t border-gray-700 sticky bottom-0 bg-gray-800"
          >
            <SlidersHorizontal className="w-3.5 h-3.5" />
            Open Tools and Settings for everything else
          </button>
        </div>
      )}
    </div>
  )
}

export default ToolSelector
