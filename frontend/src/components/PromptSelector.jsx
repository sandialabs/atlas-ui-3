import { useChat } from '../contexts/ChatContext'
import { ChevronDown, Sparkles } from 'lucide-react'
import { useState, useRef, useEffect } from 'react'

const PromptSelector = () => {
  const { prompts, selectedPrompts, setSinglePrompt } = useChat()
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef(null)

  // Get currently selected prompt
  const selectedPromptKey = selectedPrompts && selectedPrompts.size > 0
    ? Array.from(selectedPrompts)[0]
    : null

  // Find the selected prompt info
  const selectedPromptInfo = (() => {
    if (!selectedPromptKey) return null
    const idx = selectedPromptKey.indexOf('_')
    if (idx === -1) return { key: selectedPromptKey, server: 'Unknown', name: selectedPromptKey }
    const server = selectedPromptKey.slice(0, idx)
    const name = selectedPromptKey.slice(idx + 1)
    
    // Find the server and prompt details
    const promptServer = prompts.find(p => p.server === server)
    if (promptServer) {
      const prompt = promptServer.prompts?.find(p => p.name === name)
      return {
        key: selectedPromptKey,
        server,
        name,
        description: prompt?.description || ''
      }
    }
    return { key: selectedPromptKey, server, name, description: '' }
  })()

  // Get all available prompts as a flat list
  const allPrompts = []
  prompts.forEach(server => {
    if (server.prompts && server.prompts.length > 0) {
      server.prompts.forEach(prompt => {
        allPrompts.push({
          key: `${server.server}_${prompt.name}`,
          server: server.server,
          name: prompt.name,
          description: prompt.description || '',
          compliance_level: server.compliance_level
        })
      })
    }
  })

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

  // If no prompts are available, don't render anything
  if (allPrompts.length === 0) return null

  const handlePromptSelect = (promptKey) => {
    if (promptKey === selectedPromptKey) {
      // Deselect if clicking the same prompt
      setSinglePrompt(null)
    } else {
      setSinglePrompt(promptKey)
    }
    setIsOpen(false)
  }

  return (
    <div ref={dropdownRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg transition-colors text-sm border border-gray-600"
        title="Select a custom prompt"
      >
        <Sparkles className="w-4 h-4 text-purple-400" />
        <span className="max-w-[200px] truncate">
          {selectedPromptInfo ? selectedPromptInfo.name : 'Select Prompt'}
        </span>
        <ChevronDown className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen && (
        <div className="absolute bottom-full left-0 mb-2 w-80 bg-gray-800 border border-gray-600 rounded-lg shadow-lg max-h-96 overflow-y-auto z-50">
          <div className="p-2 border-b border-gray-700 bg-gray-750">
            <div className="text-xs font-semibold text-gray-300 flex items-center gap-2">
              <Sparkles className="w-3 h-3 text-purple-400" />
              Custom Prompts
            </div>
            <div className="text-xs text-gray-400 mt-1">
              Select a prompt to customize AI behavior
            </div>
          </div>

          {/* Clear selection option */}
          {selectedPromptKey && (
            <button
              onClick={() => handlePromptSelect(null)}
              className="w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 text-sm"
            >
              <div className="font-medium text-gray-400 italic">
                Clear Selection
              </div>
            </button>
          )}

          {/* Prompt list */}
          {allPrompts.map((prompt) => {
            const isSelected = prompt.key === selectedPromptKey
            return (
              <button
                key={prompt.key}
                onClick={() => handlePromptSelect(prompt.key)}
                className={`w-full px-3 py-2 text-left hover:bg-gray-700 transition-colors border-b border-gray-700 last:border-b-0 ${
                  isSelected ? 'bg-purple-900/30' : ''
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-gray-200 flex items-center gap-2">
                      {isSelected && <span className="text-purple-400">✓</span>}
                      <span className="truncate">{prompt.name}</span>
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
