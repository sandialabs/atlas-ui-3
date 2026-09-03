import { useChat } from '../contexts/ChatContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { ChevronDown, Sparkles, User, Users } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'
import { userPromptKey, isUserPromptKey, userPromptIdFromKey, personaKey, isPersonaKey, personaIdFromKey } from '../hooks/chat/useSelections'
import { getMcpNameFromKey } from '../utils/mcpKeys'

// A persona with no description falls back to its prompt text, which the admin
// can make arbitrarily long. Only two clamped lines are ever visible, so trim
// before it reaches the DOM rather than rendering 100k characters per entry.
const PERSONA_PREVIEW_CHARS = 160
const personaPreview = (content = '') =>
  content.length > PERSONA_PREVIEW_CHARS
    ? `${content.slice(0, PERSONA_PREVIEW_CHARS)}...`
    : content

const PromptSelector = () => {
  const {
    prompts, selectedPrompts, activePromptKey, makePromptActive, clearActivePrompt, removePrompts,
    userPrompts = [],
    personas = [],
    personasError = null,
    fetchPersonas,
    complianceLevelFilter = null,
    features = {},
  } = useChat()
  const { isComplianceAccessible } = useMarketplace()
  const customPromptsEnabled = !!features.custom_prompts
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef(null)

  // Personas are filtered by the current compliance context exactly like MCP
  // tools and prompts: with a filter active, a persona needs a compliance
  // level the filter allows (a level-less persona is hidden). The server
  // re-checks the same rule when resolving the turn's persona_id.
  const complianceEnabled = !!features.compliance_levels
  const visiblePersonas = (complianceEnabled && complianceLevelFilter)
    ? personas.filter(p => isComplianceAccessible(complianceLevelFilter, p.compliance_level))
    : personas

  // Get all selected prompt keys as an array (these are the "loaded" prompts)
  const selectedPromptKeys = selectedPrompts && selectedPrompts.size > 0
    ? Array.from(selectedPrompts)
    : []

  // Get only the prompts that are actually selected (loaded from Tools panel)
  const allPrompts = []
  prompts.forEach(server => {
    if (server.prompts && server.prompts.length > 0) {
      server.prompts.forEach(prompt => {
        const promptKey = `${server.server}_${prompt.name}`
        // Only include prompts that are loaded (in selectedPrompts)
        if (selectedPromptKeys.includes(promptKey)) {
          allPrompts.push({
            key: promptKey,
            server: server.server,
            name: prompt.name,
            description: prompt.description || '',
            compliance_level: server.compliance_level
          })
        }
      })
    }
  })

  // Check if default prompt is active (no active prompt key)
  const isDefaultActive = !activePromptKey

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  const handlePromptSelect = (promptKey) => {
    // Just make this prompt active without reordering
    if (makePromptActive) {
      makePromptActive(promptKey)
    }
  }

  // Get display text for the button - show the active prompt name or "Default Prompt"
  const getButtonText = () => {
    if (!activePromptKey) return 'Default Prompt'
    if (isUserPromptKey(activePromptKey)) {
      if (!customPromptsEnabled) return 'Default Prompt'
      const id = userPromptIdFromKey(activePromptKey)
      const match = userPrompts.find(p => p.id === id)
      return match ? match.title : 'Custom Prompt'
    }
    if (isPersonaKey(activePromptKey)) {
      const id = personaIdFromKey(activePromptKey)
      const match = personas.find(p => p.id === id)
      return match ? match.name : 'Persona'
    }
    return getMcpNameFromKey(activePromptKey, prompts)
  }

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-1 text-xs text-gray-400 hover:text-purple-400 transition-colors"
        title="Select custom prompts"
      >
        <Sparkles className="w-3 h-3" />
        <span className="underline decoration-dotted">
          {getButtonText()}
        </span>
        <ChevronDown className={`w-3 h-3 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute bottom-full left-0 mb-1 w-80 bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-96 overflow-y-auto z-50">
          <div className="p-2 border-b border-gray-700 bg-gray-750">
            <div className="text-xs font-semibold text-gray-300 flex items-center gap-2">
              <Sparkles className="w-3 h-3 text-purple-400" />
              Custom Prompts
            </div>
            <div className="text-xs text-gray-400 mt-1">
              Select prompts to customize AI behavior
            </div>
          </div>

          {/* Default Prompt option - always available */}
          <button
            onClick={() => {
              // Clear the active prompt to use default (but keep prompts loaded)
              if (clearActivePrompt) {
                clearActivePrompt()
              }
              setIsOpen(false)
            }}
            className={`w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 ${
              isDefaultActive ? 'bg-blue-900/30' : ''
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="font-medium text-gray-200 flex items-center gap-2">
                  {isDefaultActive && <span className="text-blue-400">✓</span>}
                  <span className="truncate">Default Prompt</span>
                  {isDefaultActive && <span className="text-xs text-blue-400">(active)</span>}
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  Use the standard system prompt without customization
                </div>
              </div>
            </div>
          </button>

          {/* Clear all selection option - only show if prompts are selected */}
          {selectedPromptKeys.length > 1 && (
            <button
              onClick={() => {
                if (removePrompts) {
                  removePrompts(selectedPromptKeys)
                }
                setIsOpen(false)
              }}
              className="w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 text-sm"
            >
              <div className="font-medium text-gray-400 italic">
                Clear All ({selectedPromptKeys.length})
              </div>
            </button>
          )}

          {/* Prompt list */}
          {allPrompts.map((prompt) => {
            const isActive = prompt.key === activePromptKey
            return (
              <button
                key={prompt.key}
                onClick={() => handlePromptSelect(prompt.key)}
                className={`w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 last:border-b-0 ${
                  isActive ? 'bg-blue-900/30' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-gray-200 flex items-center gap-2">
                      {isActive && <span className="text-blue-400">✓</span>}
                      <span className="truncate">{prompt.name}</span>
                      {isActive && <span className="text-xs text-blue-400">(active)</span>}
                    </div>
                    {prompt.description && (
                      <div className="text-xs text-gray-400 mt-1 line-clamp-2">
                        {prompt.description}
                      </div>
                    )}
                    <div className="text-xs text-gray-500 mt-1">
                      from {prompt.server}
                    </div>
                  </div>
                </div>
              </button>
            )
          })}

          {/* Admin-preconfigured personas (issue #880) */}
          {/* A failed load must not look like "no personas configured": say so
              and offer a retry instead of rendering nothing at all. */}
          {personasError && personas.length === 0 && (
            <div className="p-2 border-b border-t border-gray-700 bg-gray-750">
              <div className="text-xs font-semibold text-gray-300 flex items-center gap-2">
                <Users className="w-3 h-3 text-amber-400" />
                Personas
              </div>
              <div className="text-xs text-red-400 mt-1">
                Could not load personas ({personasError})
              </div>
              {fetchPersonas && (
                <button
                  onClick={fetchPersonas}
                  className="mt-1 text-xs text-blue-400 hover:text-blue-300 underline"
                >
                  Retry
                </button>
              )}
            </div>
          )}
          {visiblePersonas.length > 0 && (
            <>
              <div className="p-2 border-b border-t border-gray-700 bg-gray-750">
                <div className="text-xs font-semibold text-gray-300 flex items-center gap-2">
                  <Users className="w-3 h-3 text-amber-400" />
                  Personas
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  Preconfigured prompts provided by your administrator
                </div>
              </div>
              {visiblePersonas.map((p) => {
                const key = personaKey(p.id)
                const isActive = key === activePromptKey
                return (
                  <button
                    key={key}
                    onClick={() => handlePromptSelect(key)}
                    className={`w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 last:border-b-0 ${
                      isActive ? 'bg-blue-900/30' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-gray-200 flex items-center gap-2">
                          {isActive && <span className="text-blue-400">✓</span>}
                          <span className="truncate">{p.name}</span>
                          {isActive && <span className="text-xs text-blue-400">(active)</span>}
                        </div>
                        <div className="text-xs text-gray-400 mt-1 line-clamp-2">
                          {p.description || p.preview || personaPreview(p.content)}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </>
          )}

          {/* User-authored custom prompts (issue #153) */}
          {customPromptsEnabled && userPrompts.length > 0 && (
            <>
              <div className="p-2 border-b border-t border-gray-700 bg-gray-750">
                <div className="text-xs font-semibold text-gray-300 flex items-center gap-2">
                  <User className="w-3 h-3 text-emerald-400" />
                  My Prompts
                </div>
                <div className="text-xs text-gray-400 mt-1">
                  Your saved prompts (manage in Settings)
                </div>
              </div>
              {userPrompts.map((p) => {
                const key = userPromptKey(p.id)
                const isActive = key === activePromptKey
                return (
                  <button
                    key={key}
                    onClick={() => handlePromptSelect(key)}
                    className={`w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 last:border-b-0 ${
                      isActive ? 'bg-blue-900/30' : ''
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="font-medium text-gray-200 flex items-center gap-2">
                          {isActive && <span className="text-blue-400">✓</span>}
                          <span className="truncate">{p.title}</span>
                          {isActive && <span className="text-xs text-blue-400">(active)</span>}
                        </div>
                        <div className="text-xs text-gray-400 mt-1 line-clamp-2">
                          {p.content}
                        </div>
                      </div>
                    </div>
                  </button>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}

export default PromptSelector
