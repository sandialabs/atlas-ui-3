import { useChat } from '../contexts/ChatContext'
import { ChevronDown, Sparkles } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'

const PromptSelector = () => {
  const { prompts, selectedPrompts, activePromptKey, makePromptActive, clearActivePrompt, removePrompts } = useChat()
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef(null)

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
    // Extract prompt name from the key (format: "server_promptname")
    const idx = activePromptKey.indexOf('_')
    return idx === -1 ? activePromptKey : activePromptKey.slice(idx + 1)
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
        </div>
      )}
    </div>
  )
}

export default PromptSelector
