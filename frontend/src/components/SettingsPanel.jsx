import { X, RotateCcw } from 'lucide-react'
import { useState, useEffect } from 'react'
import { useChat } from '../contexts/ChatContext'

const SettingsPanel = ({ isOpen, onClose }) => {
  // Default settings
  const defaultSettings = {
    llmTemperature: 0.7,
    maxIterations: 10,
    agentLoopStrategy: 'think-act'
  }

  // State for settings
  const [settings, setSettings] = useState(defaultSettings)
  const [hasChanges, setHasChanges] = useState(false)

  // Also get live settings from ChatContext for always-in-sync fields
  const { settings: ctxSettings, updateSettings: updateCtxSettings } = useChat()

  // Load settings from localStorage on mount
  useEffect(() => {
    const savedSettings = localStorage.getItem('chatui-settings')
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings)
        setSettings({ ...defaultSettings, ...parsed })
      } catch (error) {
        console.error('Failed to parse saved settings:', error)
        setSettings(defaultSettings)
      }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Save settings to localStorage whenever they change
  const saveSettings = (newSettings) => {
    localStorage.setItem('chatui-settings', JSON.stringify(newSettings))
    setSettings(newSettings)
    setHasChanges(false)
  }

  const handleSettingChange = (key, value) => {
    const newSettings = { ...settings, [key]: value }
    setSettings(newSettings)
    setHasChanges(true)
  }

  const handleSave = () => {
    saveSettings(settings)
  }

  const handleReset = () => {
    setSettings(defaultSettings)
    saveSettings(defaultSettings)
  }

  const handleCancel = () => {
    // Reload settings from localStorage to discard changes
    const savedSettings = localStorage.getItem('chatui-settings')
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings)
        setSettings({ ...defaultSettings, ...parsed })
      } catch {
        setSettings(defaultSettings)
      }
    } else {
      setSettings(defaultSettings)
    }
    setHasChanges(false)
    onClose()
  }

  if (!isOpen) return null

  return (
    <div 
      className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div 
        className="bg-gray-800 rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] mx-4 flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-6 border-b border-gray-700 flex-shrink-0">
          <h2 className="text-xl font-semibold text-gray-100">Settings</h2>
          <button
            onClick={onClose}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          >
            <X className="w-6 h-6" />
          </button>
        </div>

        {/* Settings Content */}
        <div className="flex-1 overflow-y-auto custom-scrollbar min-h-0 p-6 space-y-6">
          {/* LLM Temperature Setting */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-white font-medium">LLM Temperature</label>
              <span className="text-sm text-gray-400 bg-gray-700 px-2 py-1 rounded">
                {settings.llmTemperature}
              </span>
            </div>
            <div className="space-y-2">
              <input
                type="range"
                min="0"
                max="1"
                step="0.1"
                value={settings.llmTemperature}
                onChange={(e) => handleSettingChange('llmTemperature', parseFloat(e.target.value))}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
              />
              <div className="flex justify-between text-xs text-gray-400">
                <span>0 (Deterministic)</span>
                <span>0.5 (Balanced)</span>
                <span>1 (Creative)</span>
              </div>
              <p className="text-sm text-gray-400">
                Controls randomness in AI responses. Lower values are more focused and deterministic, 
                higher values are more creative and varied.
              </p>
            </div>
          </div>

          {/* Max Iterations Setting */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-white font-medium">Max Agent Iterations</label>
              <span className="text-sm text-gray-400 bg-gray-700 px-2 py-1 rounded">
                {settings.maxIterations}
              </span>
            </div>
            <div className="space-y-2">
              <input
                type="range"
                min="1"
                max="50"
                step="1"
                value={settings.maxIterations}
                onChange={(e) => handleSettingChange('maxIterations', parseInt(e.target.value))}
                className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer slider"
              />
              <div className="flex justify-between text-xs text-gray-400">
                <span>1</span>
                <span>25</span>
                <span>50</span>
              </div>
              <p className="text-sm text-gray-400">
                Maximum number of iterations an agent can perform when solving complex tasks. 
                Higher values allow for more thorough problem solving but may take longer.
              </p>
            </div>
          </div>

          {/* Agent Loop Strategy Setting */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-white font-medium">Agent Loop Strategy</label>
              <span className="text-sm text-gray-400 bg-gray-700 px-2 py-1 rounded">
                {settings.agentLoopStrategy === 'agentic' ? 'Agentic' : settings.agentLoopStrategy === 'react' ? 'ReAct' : settings.agentLoopStrategy === 'act' ? 'Act' : 'Think-Act'}
              </span>
            </div>
            <div className="space-y-2">
              <select
                value={settings.agentLoopStrategy}
                onChange={(e) => handleSettingChange('agentLoopStrategy', e.target.value)}
                className="w-full px-3 py-2 bg-gray-700 text-white rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 cursor-pointer"
              >
                <option value="agentic">Agentic (Recommended)</option>
                <option value="think-act">Think-Act</option>
                <option value="react">ReAct</option>
                <option value="act">Act</option>
              </select>
              <p className="text-sm text-gray-400">
                <strong className="text-gray-300">Agentic:</strong> Claude-native loop with no control tools and tool_choice=auto.
                The model decides when to call tools and when to respond. Best for Anthropic models.
              </p>
              <p className="text-sm text-gray-400">
                <strong className="text-gray-300">Think-Act:</strong> Concise, unified reasoning approach.
                Faster iterations with fewer LLM calls. Better for most workflows and quick tasks.
              </p>
              <p className="text-sm text-gray-400">
                <strong className="text-gray-300">ReAct:</strong> Structured reasoning with Reason-Act-Observe phases.
                Better for complex tasks requiring multiple tools and detailed planning. Slower but more thorough.
              </p>
              <p className="text-sm text-gray-400">
                <strong className="text-gray-300">Act:</strong> Pure action loop without explicit reasoning steps.
                Fastest strategy with minimal overhead. LLM calls tools directly and signals completion via the "finished" tool.
              </p>
            </div>
          </div>

          {/* Auto-Approve Tools Setting (always in sync via context) */}
          <div className="bg-gray-700 rounded-lg p-4">
            <div className="flex items-center justify-between mb-2">
              <label className="text-white font-medium">Auto-Approve Tool Calls</label>
              <button
                onClick={() => updateCtxSettings({ autoApproveTools: !ctxSettings?.autoApproveTools })}
                className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                  ctxSettings?.autoApproveTools ? 'bg-green-600' : 'bg-gray-600'
                }`}
              >
                <span
                  className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                    ctxSettings?.autoApproveTools ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>
            <p className="text-sm text-gray-400">
              When enabled, tools that don't require admin approval will execute automatically without prompting.
              Tools that require admin approval will still prompt for confirmation.
            </p>
            {!ctxSettings?.autoApproveTools && (
              <p className="text-sm text-yellow-400 mt-2">
                <strong>⚠ Currently:</strong> You will be prompted to approve all tool calls unless admin has disabled approval for specific tools.
              </p>
            )}
          </div>

          {/* Future Settings Placeholder */}
          <div className="pt-4 border-t border-gray-700">
            <h3 className="text-lg font-medium text-gray-300 mb-3">Coming Soon</h3>
            <div className="space-y-3 opacity-50">
              <div className="p-3 bg-gray-700 rounded-lg">
                <div className="text-sm text-gray-400">More customization options will be added here</div>
              </div>
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="flex items-center justify-between p-6 border-t border-gray-700 flex-shrink-0">
          <button
            onClick={handleReset}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
          >
            <RotateCcw className="w-4 h-4" />
            Reset to Defaults
          </button>
          
          <div className="flex items-center gap-3">
            <button
              onClick={handleCancel}
              className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={!hasChanges}
              className={`px-4 py-2 rounded-lg transition-colors font-medium ${
                hasChanges
                  ? 'bg-blue-600 hover:bg-blue-700 text-white'
                  : 'bg-gray-600 text-gray-400 cursor-not-allowed'
              }`}
            >
              Save Changes
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default SettingsPanel
