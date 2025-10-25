import { useState } from 'react'
import { ChevronDown, ChevronRight, CheckCircle, XCircle, Clock, Zap } from 'lucide-react'

const AgentAction = ({ message }) => {
  const [isExpanded, setIsExpanded] = useState(false)
  const [resultExpanded, setResultExpanded] = useState(false)

  // Special handling for completion tool
  const isCompletionTool = message.tool_name === 'all_work_is_done'
  
  const getStatusIcon = () => {
    switch (message.status) {
      case 'calling':
        return <Clock className="w-4 h-4 text-blue-400 animate-pulse" />
      case 'completed':
        return isCompletionTool 
          ? <CheckCircle className="w-4 h-4 text-green-400" />
          : <CheckCircle className="w-4 h-4 text-green-400" />
      case 'failed':
        return <XCircle className="w-4 h-4 text-red-400" />
      default:
        return <Clock className="w-4 h-4 text-gray-400" />
    }
  }

  const getStatusColor = () => {
    if (isCompletionTool) {
      return 'border-l-green-500 bg-green-900/10'
    }
    switch (message.status) {
      case 'calling':
        return 'border-l-blue-500 bg-blue-900/10'
      case 'completed':
        return 'border-l-green-500 bg-green-900/10'
      case 'failed':
        return 'border-l-red-500 bg-red-900/10'
      default:
        return 'border-l-gray-500 bg-gray-900/10'
    }
  }

  const getActionTitle = () => {
    if (isCompletionTool) {
      return 'Agent Completed Task'
    }
    return `Agent Action: ${message.tool_name}`
  }

  const getActionDescription = () => {
    if (isCompletionTool) {
      return 'The agent has finished all required work'
    }
    return `Using ${message.tool_name} from ${message.server_name}`
  }

  const formatArguments = (args) => {
    if (!args || Object.keys(args).length === 0) return null
    
    return Object.entries(args).map(([key, value]) => (
      <div key={key} className="flex gap-2 text-sm">
        <span className="text-gray-400 font-medium min-w-20">{key}:</span>
        <span className="text-gray-200 break-all">
          {typeof value === 'string' && value.length > 100 
            ? `${value.substring(0, 100)}...` 
            : JSON.stringify(value)}
        </span>
      </div>
    ))
  }

  return (
    <div className={`border-l-4 rounded-r-lg p-4 mb-4 ${getStatusColor()}`}>
      {/* Header */}
      <div className="flex items-center gap-3 mb-2">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-purple-400" />
          <span className="text-xs font-medium px-2 py-1 bg-purple-600 text-white rounded">
            AGENT
          </span>
        </div>
        {getStatusIcon()}
        <div className="flex-1">
          <div className="font-medium text-gray-200">
            {getActionTitle()}
          </div>
          <div className="text-sm text-gray-400">
            {getActionDescription()}
          </div>
        </div>
        <button
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-gray-400 hover:text-gray-200 transition-colors"
        >
          {isExpanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
      </div>

      {/* Status message for completion tool */}
      {isCompletionTool && message.status === 'completed' && (
        <div className="text-sm text-green-300 bg-green-900/20 rounded p-2 mb-2">
          Task completed successfully. The agent has finished all required work.
        </div>
      )}

      {/* Expanded details */}
      {isExpanded && (
        <div className="mt-3 space-y-3 border-t border-gray-700 pt-3">
          {/* Arguments */}
          {message.arguments && Object.keys(message.arguments).length > 0 && (
            <div>
              <h4 className="text-sm font-medium text-gray-300 mb-2">Parameters:</h4>
              <div className="bg-gray-800/50 rounded p-3 space-y-1 text-xs font-mono">
                {formatArguments(message.arguments)}
              </div>
            </div>
          )}

          {/* Result */}
          {message.result && (
            <div>
              <div className="flex items-center gap-2 mb-2">
                <h4 className="text-sm font-medium text-gray-300">Result:</h4>
                <button
                  onClick={() => setResultExpanded(!resultExpanded)}
                  className="text-xs text-gray-400 hover:text-gray-200 transition-colors"
                >
                  {resultExpanded ? 'Collapse' : 'Expand'}
                </button>
              </div>
              <div className={`bg-gray-800/50 rounded p-3 text-xs font-mono text-gray-200 ${
                resultExpanded ? '' : 'max-h-32 overflow-hidden'
              }`}>
                <pre className="whitespace-pre-wrap break-words">
                  {typeof message.result === 'string' 
                    ? message.result 
                    : JSON.stringify(message.result, null, 2)}
                </pre>
              </div>
            </div>
          )}

          {/* Timestamp */}
          {message.timestamp && (
            <div className="text-xs text-gray-500">
              {new Date(message.timestamp).toLocaleTimeString()}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default AgentAction