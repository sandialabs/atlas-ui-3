import { ExternalLink, Eye, Wrench, Brain } from 'lucide-react'

function formatContextWindow(tokens) {
  if (!tokens) return null
  if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}M tokens`
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K tokens`
  return `${tokens} tokens`
}

export default function ModelInfoPopover({ model }) {
  if (!model) return null

  const contextStr = formatContextWindow(model.context_window)

  return (
    <div
      className="absolute right-0 top-0 mt-0 w-64 bg-gray-900 border border-gray-600 rounded-lg shadow-xl p-3 text-sm z-[60]"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="font-medium text-gray-100 mb-1">{model.name}</div>

      {model.description && (
        <p className="text-gray-400 text-xs mb-2">{model.description}</p>
      )}

      {contextStr && (
        <div className="text-gray-300 text-xs mb-2">
          Context: {contextStr}
        </div>
      )}

      {(model.supports_vision || model.supports_tools || model.supports_reasoning) && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {model.supports_vision && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-blue-900 text-blue-300 rounded text-xs">
              <Eye className="w-3 h-3" /> Vision
            </span>
          )}
          {model.supports_tools && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-green-900 text-green-300 rounded text-xs">
              <Wrench className="w-3 h-3" /> Tools
            </span>
          )}
          {model.supports_reasoning && (
            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 bg-purple-900 text-purple-300 rounded text-xs">
              <Brain className="w-3 h-3" /> Reasoning
            </span>
          )}
        </div>
      )}

      {model.compliance_level && (
        <div className="text-xs text-gray-400 mb-2">
          Compliance: <span className="text-blue-300">{model.compliance_level}</span>
        </div>
      )}

      {model.model_card_url && (
        <a
          href={model.model_card_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 transition-colors"
        >
          <ExternalLink className="w-3 h-3" />
          View Model Card
        </a>
      )}
    </div>
  )
}
