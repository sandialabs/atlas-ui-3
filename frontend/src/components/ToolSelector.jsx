import { useCallback, useId, useMemo, useState, useRef, useEffect } from 'react'
import { Wrench, ChevronDown, Search, Server } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'
import { useOptionalMarketplace } from '../contexts/MarketplaceContext'
import { openSettingsPanel } from '../utils/settingsPanelEvents'
import {
  SEARCH_AUTO_ENABLED_NOTE,
  SEARCH_TOOL_KEY,
  searchToolAutoEnabled
} from '../utils/searchToolAvailability'
import { useEscapeKey } from '../hooks/useEscapeKey'
import { FOCUS_RING } from '../utils/focusRing'
import ToolsSettingsIcon from './ToolsSettingsIcon'

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
  const {
    tools: allTools = [],
    selectedTools,
    toggleTool,
    features,
    complianceLevelFilter,
    selectedDataSources,
    ragEnabled,
    ragSources
  } = useChat()
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
  const searchRef = useRef(null)
  const popoverId = useId()

  // Opening the menu puts focus in the search box, so keyboard users land
  // inside it instead of having to Tab past the composer to reach the rows.
  // Not on a touch or pen open: focusing a text field raises the on-screen
  // keyboard, which covers most of the popover's rows.
  //
  // The pointer type is noted at pointerdown but only acted on by the click
  // it produces: a pointerdown that never becomes a click (a cancelled tap, a
  // drag off the button) is dropped on pointercancel or by age, and every
  // click consumes it, so a later screen-reader or keyboard open -- no
  // pointerdown at all -- still focuses the search box.
  const lastPointerRef = useRef(null)
  const skipSearchFocusRef = useRef(false)
  const POINTER_CLICK_WINDOW_MS = 1500
  const handleTriggerClick = () => {
    const pointer = lastPointerRef.current
    lastPointerRef.current = null
    skipSearchFocusRef.current = Boolean(
      pointer && pointer.type !== 'mouse' && Date.now() - pointer.at < POINTER_CLICK_WINDOW_MS
    )
    setIsOpen(!isOpen)
  }
  useEffect(() => {
    const skip = skipSearchFocusRef.current
    skipSearchFocusRef.current = false
    if (isOpen && !skip) searchRef.current?.focus()
  }, [isOpen])

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

  // The built-in search tool is on because the user selected data sources
  // (#1102): the server offers it for the very same reason, so the row is
  // shown as on and cannot be switched off while the sources are selected.
  const searchAutoEnabled = searchToolAutoEnabled({
    tools,
    selectedDataSources,
    ragEnabled,
    reachableSourceCount: (ragSources || []).length
  })

  if (!features?.tools) return null

  // The count is the effective one: the implied search tool is on, so it
  // counts as one of the tools the model can use.
  const selectedCount = (selectedTools?.size || 0) + (searchAutoEnabled ? 1 : 0)

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        ref={triggerRef}
        onPointerDown={(e) => { lastPointerRef.current = { type: e.pointerType, at: Date.now() } }}
        onPointerCancel={() => { lastPointerRef.current = null }}
        onClick={handleTriggerClick}
        className={`flex items-center gap-1 text-xs rounded transition-colors ${FOCUS_RING} ${
          isOpen || selectedCount > 0 ? 'text-blue-400 hover:text-blue-300' : 'text-gray-400 hover:text-blue-400'
        }`}
        title="Choose which tools the model can use"
        aria-expanded={isOpen}
        aria-haspopup="dialog"
        aria-controls={isOpen ? popoverId : undefined}
      >
        <Wrench className="w-3 h-3" aria-hidden="true" />
        <span className="underline decoration-dotted">
          {selectedCount > 0 ? `${selectedCount} tool${selectedCount === 1 ? '' : 's'}` : 'Tools'}
        </span>
        <ChevronDown aria-hidden="true" className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div
          id={popoverId}
          role="dialog"
          aria-label="Tools"
          className="absolute bottom-full left-0 mb-1 w-96 max-w-[90vw] bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-96 overflow-y-auto z-50"
        >
          <div className="p-2 border-b border-gray-700 sticky top-0 bg-gray-800">
            <div className="relative">
              <Search aria-hidden="true" className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
              <input
                ref={searchRef}
                type="search"
                aria-label="Search tools"
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
                <Server aria-hidden="true" className="w-3 h-3 text-gray-400" />
                {group.server}
              </div>
              {group.rows.map(row => {
                const autoEnabled = row.key === SEARCH_TOOL_KEY && searchAutoEnabled
                const isOn = selectedTools?.has(row.key) || autoEnabled
                return (
                  <button
                    key={row.key}
                    type="button"
                    onClick={autoEnabled ? undefined : () => toggleTool(row.key)}
                    aria-pressed={isOn}
                    aria-disabled={autoEnabled ? true : undefined}
                    title={autoEnabled
                      ? SEARCH_AUTO_ENABLED_NOTE
                      : `${isOn ? 'Turn off' : 'Turn on'} ${row.name}`}
                    className={`w-full px-3 py-2 text-left flex items-start gap-2 border-b border-gray-700 last:border-b-0 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400 ${
                      isOn ? 'bg-blue-900/30 hover:bg-blue-900/40' : 'hover:bg-gray-700'
                    }`}
                  >
                    <Wrench aria-hidden="true" className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${isOn ? 'text-blue-400' : 'text-gray-500'}`} />
                    <span className="flex-1 min-w-0">
                      <span className="block text-sm text-gray-200 break-words">{row.name}</span>
                      {row.description && (
                        <span className="block text-xs text-gray-400 break-words">{row.description}</span>
                      )}
                      {autoEnabled && (
                        <span className="block text-xs text-blue-300 break-words">{SEARCH_AUTO_ENABLED_NOTE}</span>
                      )}
                    </span>
                    {/* Toggle, not a checkbox: the reviewer asked for tools that
                        read as on/off at a glance. */}
                    <span
                      aria-hidden="true"
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
            className="group w-full px-3 py-2 flex items-center gap-2 text-xs text-gray-300 hover:bg-gray-700 border-t border-gray-700 sticky bottom-0 bg-gray-800 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-400"
          >
            <ToolsSettingsIcon size="sm" badgeBg="bg-gray-800 group-hover:bg-gray-700" />
            Open Tools and Settings for everything else
          </button>
        </div>
      )}
    </div>
  )
}

export default ToolSelector
