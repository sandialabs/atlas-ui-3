import { X } from 'lucide-react'
import { useChat } from '../contexts/ChatContext'

const AgentModal = ({ isOpen, onClose }) => {
  const {
    agentModeEnabled,
    setAgentModeEnabled,
    agentMaxSteps,
    setAgentMaxSteps,
    currentAgentStep,
    agentModeAvailable
  } = useChat()

  if (!agentModeAvailable) return null

  const progressPercent = currentAgentStep > 0 ? (currentAgentStep / agentMaxSteps) * 100 : 0

  return (
    <>
      {/* Backdrop */}
      {isOpen && (
        <div 
          className="fixed inset-0 bg-black bg-opacity-75 backdrop-blur-sm z-50 flex items-center justify-center"
          onClick={onClose}
        >
          {/* Modal */}
          <div 
            className="bg-gray-800 border border-gray-600 rounded-xl shadow-2xl w-full max-w-md mx-4 max-h-[80vh] overflow-hidden"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between p-6 border-b border-gray-700 bg-gray-900">
              <h2 className="text-xl font-semibold text-gray-100">Agent Settings</h2>
              <button
                onClick={onClose}
                className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Body */}
            <div className="p-6 space-y-6 overflow-y-auto">
              {/* Agent Mode Toggle */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="flex items-center gap-3 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={agentModeEnabled}
                      onChange={(e) => setAgentModeEnabled(e.target.checked)}
                      className="w-5 h-5 text-blue-600 bg-gray-700 border-gray-600 rounded focus:ring-blue-500 focus:ring-2"
                    />
                    <div className="flex flex-col">
                      <span className="text-lg font-medium text-gray-100">Agent Mode</span>
                      <span className={`text-sm ${agentModeEnabled ? 'text-blue-400' : 'text-gray-400'}`}>
                        {agentModeEnabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </div>
                  </label>
                </div>
                
                <p className="text-sm text-gray-400">
                  Enable multi-step reasoning where the AI can think through problems step by step, 
                  using tools and data sources as needed.
                </p>
              </div>

              {/* Max Steps Slider */}
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <label className="text-sm font-medium text-gray-200">
                    Max Steps:
                  </label>
                  <span className="text-sm font-medium text-blue-400">
                    {agentMaxSteps}
                  </span>
                </div>
                
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={agentMaxSteps}
                  onChange={(e) => setAgentMaxSteps(parseInt(e.target.value))}
                  className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
                />
                
                <div className="flex justify-between text-xs text-gray-400">
                  <span>1</span>
                  <span>5</span>
                  <span>10</span>
                </div>
              </div>

              {/* Agent Progress */}
              {currentAgentStep > 0 && (
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-gray-200">Agent Progress</span>
                    <span className="text-sm text-gray-400">
                      Step {currentAgentStep} of {agentMaxSteps}
                    </span>
                  </div>
                  
                  <div className="w-full bg-gray-700 rounded-full h-2">
                    <div 
                      className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                      style={{ width: `${progressPercent}%` }}
                    />
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}

    </>
  )
}

export default AgentModal