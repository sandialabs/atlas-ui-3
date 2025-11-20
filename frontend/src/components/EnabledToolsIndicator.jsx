import { useChat } from '../contexts/ChatContext'
import { X } from 'lucide-react'

const EnabledToolsIndicator = () => {
  const { selectedTools, toggleTool } = useChat()

  const allTools = Array.from(selectedTools).map(key => {
    const parts = key.split('_')
    return { name: parts.slice(1).join('_'), key, type: 'tool' }
  })

  // Only show tools (prompts are now in the PromptSelector)
  if (allTools.length === 0) return null

  return (
    <div className="flex items-start gap-2 text-xs text-gray-400 mb-2">
      <span className="mt-1">Active Tools:</span>
      <div className="flex-1 flex flex-wrap gap-1">
        {allTools.map((item, idx) => (
          <div
            key={idx}
            className="px-2 py-1 rounded flex items-center gap-1 bg-gray-700 text-gray-300"
          >
            <span>{item.name}</span>
            <button
              onClick={() => toggleTool(item.key)}
              className="hover:bg-red-600 hover:bg-opacity-50 rounded p-0.5 transition-colors"
              title={`Remove ${item.name}`}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

export default EnabledToolsIndicator