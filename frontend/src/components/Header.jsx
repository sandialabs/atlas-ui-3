import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import { useLLMAuthStatus } from '../hooks/useLLMAuthStatus'
import TokenInputModal from './TokenInputModal'
import { Database, ChevronDown, Wrench, Bot, Download, Plus, HelpCircle, Shield, FolderOpen, Monitor, Settings, Menu, X, Key, PanelLeft, HardDrive, Cloud } from 'lucide-react'
import { nextSaveMode } from '../utils/saveModeConfig'

// Save mode display config: label, icon component, button classes, title text
const SAVE_MODE_CONFIG = {
  none: {
    label: 'Incognito',
    Icon: Database,
    strikethrough: true,
    btnClass: 'bg-red-700 hover:bg-red-600 text-white',
    title: 'Incognito -- conversations not saved anywhere (click to cycle)',
  },
  local: {
    label: 'Saved Locally',
    Icon: HardDrive,
    strikethrough: false,
    btnClass: 'bg-gray-700 hover:bg-gray-600 text-blue-300',
    title: 'Conversations saved in your browser (click to cycle)',
  },
  server: {
    label: 'Saved to Server',
    Icon: Cloud,
    strikethrough: false,
    btnClass: 'bg-gray-700 hover:bg-gray-600 text-green-400',
    title: 'Conversations saved to server (click to cycle)',
  },
}

const Header = ({ onToggleSidebar, onToggleRag, onToggleTools, onToggleFiles, onToggleCanvas, onCloseCanvas, onToggleSettings }) => {
  const navigate = useNavigate()
  const {
    user,
    models,
    currentModel,
    setCurrentModel,
    agentModeAvailable,
    agentModeEnabled,
    setAgentModeEnabled,
    saveMode,
    setSaveMode,
    downloadChat,
    downloadChatAsText,
    messages,
    clearChat,
    features,
    isInAdminGroup,
    complianceLevelFilter,
    setComplianceLevelFilter,
    selectedDataSources
  } = useChat()
  const { isComplianceAccessible, complianceLevels } = useMarketplace()
  const { connectionStatus, isConnected } = useWS()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [downloadDropdownOpen, setDownloadDropdownOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [llmAuthModalModel, setLlmAuthModalModel] = useState(null)
  const llmAuth = useLLMAuthStatus()

  // Fetch LLM auth status on mount
  useEffect(() => {
    llmAuth.fetchAuthStatus()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  
  // Extract unique compliance levels from all available tools and prompts
  const availableComplianceLevels = complianceLevels.map(l => l.name)

  const handleModelSelect = (model) => {
    setCurrentModel(model)
    setDropdownOpen(false)
  }

  // Close dropdowns when mobile menu opens
  useEffect(() => {
    if (mobileMenuOpen) {
      setDropdownOpen(false)
      setDownloadDropdownOpen(false)
    }
  }, [mobileMenuOpen])

  // Handle Escape key to close mobile menu
  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape') setMobileMenuOpen(false)
    }
    if (mobileMenuOpen) {
      document.addEventListener('keydown', handleEscape)
      return () => document.removeEventListener('keydown', handleEscape)
    }
  }, [mobileMenuOpen])

  // Handle hotkey for new chat (Ctrl+Alt+N)
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.altKey && (event.key === 'N' || event.key === 'n')) {
        event.preventDefault()
        event.stopPropagation()
        clearChat()
        onCloseCanvas()
        // Focus the message input after a brief delay
        setTimeout(() => {
          const messageInput = document.querySelector('textarea[placeholder*="message"]')
          if (messageInput) {
            messageInput.focus()
          }
        }, 100)
      }
    }

    document.addEventListener('keydown', handleKeyDown, true) // Use capture phase
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clearChat])

  return (
    <header className="flex items-center justify-between p-2 sm:p-4 bg-gray-800 border-b border-gray-700">
      {/* Left section */}
      <div className="flex items-center gap-2 sm:gap-4">
        {/* Mobile sidebar toggle */}
        {features?.chat_history && (
          <button
            onClick={onToggleSidebar}
            className="md:hidden p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-200"
            title="Conversations"
          >
            <PanelLeft className="w-5 h-5" />
          </button>
        )}

        {features?.rag && (
          <button
            onClick={onToggleRag}
            className={`flex items-center gap-2 px-2 sm:px-3 py-2 rounded-lg transition-colors ${
              selectedDataSources?.size > 0
                ? 'bg-blue-600 hover:bg-blue-700 text-white'
                : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
            }`}
            title="Toggle Data Sources"
          >
            <Database className="w-4 h-4 sm:w-5 sm:h-5" />
            <span className="text-sm font-medium hidden lg:inline">
              {selectedDataSources?.size > 0 ? `${selectedDataSources.size} sources` : 'Sources'}
            </span>
          </button>
        )}
        
        {/* New Chat Button */}
        <button
          onClick={() => {
            clearChat()
            onCloseCanvas()
            // Focus the message input after a brief delay
            setTimeout(() => {
              const messageInput = document.querySelector('textarea[placeholder*="message"]')
              if (messageInput) {
                messageInput.focus()
              }
            }, 100)
          }}
          className="flex items-center gap-2 px-2 sm:px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-200"
          title="Start New Chat (Ctrl+Alt+N)"
        >
          <Plus className="w-4 h-4 sm:w-5 sm:h-5" />
          <span className="text-sm font-medium hidden md:inline">New Chat</span>
        </button>
      </div>

      {/* Right section */}
      <div className="flex items-center gap-2 sm:gap-4">
        {/* Model Selection Dropdown - Always visible but more compact on mobile */}
        <div className="relative">
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="flex items-center gap-1 sm:gap-2 px-2 sm:px-4 py-2 bg-gray-700 rounded-lg hover:bg-gray-600 transition-colors min-w-[80px] sm:min-w-[160px]"
          >
            <span className="text-xs sm:text-sm text-gray-200 truncate">
              {currentModel || 'Model...'}
            </span>
            {(() => {
              const cm = models.find(m => (typeof m === 'string' ? m : m.name) === currentModel)
              const cmObj = typeof cm === 'string' ? { name: cm } : cm
              if (cmObj?.api_key_source === 'user') {
                const hasKey = cmObj.user_has_key || llmAuth.getModelAuth(currentModel)?.authenticated
                return <Key className={`w-3 h-3 flex-shrink-0 ${hasKey ? 'text-green-400' : 'text-orange-400'}`} />
              }
              return null
            })()}
            <ChevronDown className="w-3 h-3 sm:w-4 sm:h-4 flex-shrink-0" />
          </button>
          
          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-1 w-56 sm:w-64 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50 max-h-96 overflow-y-auto">
              {models.length === 0 ? (
                <div className="px-4 py-2 text-gray-400 text-sm">No models available</div>
              ) : (
                (() => {
                  // Filter models by compliance level if feature is enabled
                  const complianceEnabled = features?.compliance_levels
                  const filteredModels = complianceEnabled && complianceLevelFilter
                    ? models.filter(m => {
                        const model = typeof m === 'string' ? { name: m } : m
                        // STRICT MODE: When compliance filter is active, only show models with matching compliance levels
                        return isComplianceAccessible(complianceLevelFilter, model.compliance_level)
                      })
                    : models
                  
                  return filteredModels.map(m => {
                    const model = typeof m === 'string' ? { name: m } : m
                    const modelName = model.name || m
                    const needsUserKey = model.api_key_source === 'user'
                    const hasUserKey = model.user_has_key === true || llmAuth.getModelAuth(modelName)?.authenticated === true
                    const isDisabled = needsUserKey && !hasUserKey
                    return (
                      <div
                        key={modelName}
                        className="flex items-center first:rounded-t-lg last:rounded-b-lg"
                      >
                        <button
                          onClick={() => !isDisabled && handleModelSelect(modelName)}
                          className={`flex-1 text-left px-4 py-2 text-sm flex items-center justify-between gap-2 ${
                            isDisabled
                              ? 'text-gray-500 cursor-not-allowed'
                              : 'text-gray-200 hover:bg-gray-700'
                          }`}
                          disabled={isDisabled}
                          title={isDisabled ? 'Configure your API key to use this model' : modelName}
                        >
                          <span className="truncate">{modelName}</span>
                          <span className="flex items-center gap-1 flex-shrink-0">
                            {complianceEnabled && model.compliance_level && (
                              <span className="px-1.5 py-0.5 bg-blue-600 text-xs rounded text-white flex items-center gap-1">
                                <Shield className="w-3 h-3" />
                                {model.compliance_level}
                              </span>
                            )}
                          </span>
                        </button>
                        {needsUserKey && (
                          <button
                            onClick={(e) => {
                              e.stopPropagation()
                              setLlmAuthModalModel(modelName)
                              setDropdownOpen(false)
                            }}
                            className="px-2 py-2 hover:bg-gray-700 transition-colors"
                            title={hasUserKey ? 'API key configured (click to change)' : 'Click to add your API key'}
                          >
                            <Key className={`w-4 h-4 ${hasUserKey ? 'text-green-400' : 'text-orange-400'}`} />
                          </button>
                        )}
                      </div>
                    )
                  })
                })()
              )}
            </div>
          )}
        </div>

        {/* Connection Status - Show dot on all screens, text on sm+ */}
        <div className="flex items-center gap-2 text-xs">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-gray-400 hidden sm:inline">{connectionStatus}</span>
        </div>

        {/* Save Mode Toggle - cycles: Incognito -> Saved Locally -> Saved to Server */}
        {features?.chat_history && (() => {
          const cfg = SAVE_MODE_CONFIG[saveMode] || SAVE_MODE_CONFIG.server
          const { Icon } = cfg
          return (
            <button
              onClick={() => setSaveMode(nextSaveMode(saveMode))}
              className={`flex items-center gap-1.5 px-2 sm:px-3 py-2 rounded-lg text-sm font-medium transition-colors ${cfg.btnClass}`}
              title={cfg.title}
            >
              <span className="relative inline-flex items-center justify-center w-4 h-4">
                <Icon className="w-4 h-4" />
                {cfg.strikethrough && (
                  <span className="absolute inset-0 flex items-center justify-center">
                    <span className="block w-5 h-0.5 bg-current rotate-45 rounded" />
                  </span>
                )}
              </span>
              <span className="hidden sm:inline">{cfg.label}</span>
            </button>
          )
        })()}

        {/* Desktop-only buttons (hidden on mobile, shown in hamburger menu) */}
        <div className="hidden min-[1200px]:flex items-center gap-2">
          {/* User Info */}
          <div className="text-sm text-gray-300">
            {user}
          </div>

          {/* Download Chat Button */}
          <div className="relative">
            <button
              onClick={() => setDownloadDropdownOpen(!downloadDropdownOpen)}
              disabled={messages.length === 0}
              className={`p-2 rounded-lg transition-colors ${
                messages.length === 0 
                  ? 'bg-gray-700 text-gray-500 cursor-not-allowed' 
                  : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
              }`}
              title="Download Chat History"
            >
              <Download className="w-5 h-5" />
            </button>
            
            {downloadDropdownOpen && messages.length > 0 && (
              <div className="absolute right-0 top-full mt-1 w-48 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50">
                <button
                  onClick={() => {
                    downloadChat()
                    setDownloadDropdownOpen(false)
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 first:rounded-t-lg"
                >
                  Download as JSON
                </button>
                <button
                  onClick={() => {
                    downloadChatAsText()
                    setDownloadDropdownOpen(false)
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 last:rounded-b-lg"
                >
                  Download as Text
                </button>
              </div>
            )}
          </div>

          {/* Compliance Level Dropdown */}
          {features?.compliance_levels && availableComplianceLevels.length > 0 && (
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-700 border border-gray-600">
              <Shield className="w-4 h-4 text-blue-400" />
              <select
                value={complianceLevelFilter || ''}
                onChange={(e) => setComplianceLevelFilter(e.target.value || null)}
                className="bg-gray-600 border border-gray-500 rounded px-2 py-1 text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                title="Select compliance level for this session"
              >
                <option value="">All Levels</option>
                {availableComplianceLevels.map(level => (
                  <option key={level} value={level}>{level}</option>
                ))}
              </select>
            </div>
          )}

          {/* Agent Mode Toggle Button */}
          {agentModeAvailable && (
            <button
              onClick={() => setAgentModeEnabled(!agentModeEnabled)}
              className={`p-2 rounded-lg transition-colors ${
                agentModeEnabled 
                  ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                  : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
              }`}
              title={agentModeEnabled ? "Agent Mode: ON (click to disable)" : "Agent Mode: OFF (click to enable)"}
            >
              <Bot className="w-5 h-5" />
            </button>
          )}

          {/* Admin Button - Only show for admin users */}
          {isInAdminGroup && (
            <button
              onClick={() => navigate('/admin')}
              className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              title="Admin Dashboard"
            >
              <Shield className="w-5 h-5" />
            </button>
          )}

          {/* Settings Button */}
          <button
            onClick={onToggleSettings}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Settings"
          >
            <Settings className="w-5 h-5" />
          </button>

          {/* Help Button */}
          <button
            onClick={() => navigate('/help')}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Help & Documentation"
          >
            <HelpCircle className="w-5 h-5" />
          </button>

          {/* Tools Panel Toggle */}
          {(() => {
            if (features?.tools) {
              return (
                <button
                  onClick={onToggleTools}
                  className="p-2 rounded-lg bg-yellow-500 border border-red-500 block"
                  title="Toggle Tools, Integrations, and Prompts"
                >
                  <Wrench className="w-5 h-5" />
                </button>
              );
            } else {
              return null; // Render nothing if not showing
            }
          })()}
          
          {/* File Manager Panel Toggle */}
          {features?.files_panel && (
            <button
              onClick={onToggleFiles}
              className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              title="File Manager"
            >
              <FolderOpen className="w-5 h-5" />
            </button>
          )}
          
          {/* Canvas Panel Toggle */}
          <button
            onClick={onToggleCanvas}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Toggle Canvas"
          >
            <Monitor className="w-5 h-5" />
          </button>
        </div>

        {/* Hamburger Menu Button - Only visible on mobile/tablet */}
        <button
          onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
          className="min-[1200px]:hidden p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
          title="Menu"
          aria-expanded={mobileMenuOpen}
          aria-controls="mobile-menu"
          aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
        >
          {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {/* Mobile Menu Overlay */}
      {mobileMenuOpen && (
        <>
          {/* Backdrop */}
          <div 
            className="fixed inset-0 bg-black bg-opacity-50 z-40 min-[1200px]:hidden" 
            onClick={() => setMobileMenuOpen(false)}
          />
          
          {/* Menu Panel */}
          <div id="mobile-menu" className="fixed top-[57px] sm:top-[65px] right-0 w-64 bg-gray-800 border-l border-gray-700 shadow-lg z-50 min-[1200px]:hidden max-h-[calc(100vh-57px)] sm:max-h-[calc(100vh-65px)] overflow-y-auto">
            <div className="p-4 space-y-2">
              {/* User Info */}
              <div className="px-3 py-2 text-sm text-gray-300 bg-gray-700 rounded-lg">
                User: {user}
              </div>

              {/* Download Chat */}
              <button
                onClick={() => {
                  downloadChat()
                  setMobileMenuOpen(false)
                }}
                disabled={messages.length === 0}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  messages.length === 0
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
              >
                <Download className="w-5 h-5" />
                <span>Download as JSON</span>
              </button>

              <button
                onClick={() => {
                  downloadChatAsText()
                  setMobileMenuOpen(false)
                }}
                disabled={messages.length === 0}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  messages.length === 0
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
              >
                <Download className="w-5 h-5" />
                <span>Download as Text</span>
              </button>

              {/* Compliance Level */}
              {features?.compliance_levels && availableComplianceLevels.length > 0 && (
                <div className="px-3 py-2 bg-gray-700 rounded-lg">
                  <div className="flex items-center gap-2 mb-2">
                    <Shield className="w-4 h-4 text-blue-400" />
                    <span className="text-sm text-gray-200">Compliance Level</span>
                  </div>
                  <select
                    value={complianceLevelFilter || ''}
                    onChange={(e) => setComplianceLevelFilter(e.target.value || null)}
                    className="w-full bg-gray-600 border border-gray-500 rounded px-2 py-1 text-gray-100 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    <option value="">All Levels</option>
                    {availableComplianceLevels.map(level => (
                      <option key={level} value={level}>{level}</option>
                    ))}
                  </select>
                </div>
              )}

              {/* Save Mode Toggle (Mobile) */}
              {features?.chat_history && (() => {
                const cfg = SAVE_MODE_CONFIG[saveMode] || SAVE_MODE_CONFIG.server
                const { Icon } = cfg
                return (
                  <button
                    onClick={() => {
                      setSaveMode(nextSaveMode(saveMode))
                      setMobileMenuOpen(false)
                    }}
                    className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${cfg.btnClass}`}
                  >
                    <span className="relative inline-flex items-center justify-center w-5 h-5">
                      <Icon className="w-5 h-5" />
                      {cfg.strikethrough && (
                        <span className="absolute inset-0 flex items-center justify-center">
                          <span className="block w-6 h-0.5 bg-current rotate-45 rounded" />
                        </span>
                      )}
                    </span>
                    <span>{cfg.label}</span>
                  </button>
                )
              })()}

              {/* Agent Mode Toggle */}
              {agentModeAvailable && (
                <button
                  onClick={() => {
                    setAgentModeEnabled(!agentModeEnabled)
                    setMobileMenuOpen(false)
                  }}
                  className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                    agentModeEnabled 
                      ? 'bg-blue-600 hover:bg-blue-700 text-white' 
                      : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                  }`}
                >
                  <Bot className="w-5 h-5" />
                  <span>Agent Mode: {agentModeEnabled ? 'ON' : 'OFF'}</span>
                </button>
              )}

              {/* Admin Button */}
              {isInAdminGroup && (
                <button
                  onClick={() => {
                    navigate('/admin')
                    setMobileMenuOpen(false)
                  }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
                >
                  <Shield className="w-5 h-5" />
                  <span>Admin Dashboard</span>
                </button>
              )}

              {/* Settings Button */}
              <button
                onClick={() => {
                  onToggleSettings()
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <Settings className="w-5 h-5" />
                <span>Settings</span>
              </button>

              {/* Help Button */}
              <button
                onClick={() => {
                  navigate('/help')
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <HelpCircle className="w-5 h-5" />
                <span>Help & Documentation</span>
              </button>

              {/* Tools Panel Toggle */}
              {features?.tools && (
                <button
                  onClick={() => {
                    onToggleTools()
                    setMobileMenuOpen(false)
                  }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-yellow-500 border border-red-500 text-sm transition-colors"
                >
                  <Wrench className="w-5 h-5" />
                  <span>Tools & Prompts</span>
                </button>
              )}
              
              {/* File Manager Panel Toggle */}
              {features?.files_panel && (
                <button
                  onClick={() => {
                    onToggleFiles()
                    setMobileMenuOpen(false)
                  }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
                >
                  <FolderOpen className="w-5 h-5" />
                  <span>File Manager</span>
                </button>
              )}
              
              {/* Canvas Panel Toggle */}
              <button
                onClick={() => {
                  onToggleCanvas()
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <Monitor className="w-5 h-5" />
                <span>Canvas</span>
              </button>
            </div>
          </div>
        </>
      )}

      {/* Close dropdown when clicking outside */}
      {dropdownOpen && (
        <div 
          className="fixed inset-0 z-40" 
          onClick={() => setDropdownOpen(false)}
        />
      )}
      
      {/* Close download dropdown when clicking outside */}
      {downloadDropdownOpen && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setDownloadDropdownOpen(false)}
        />
      )}

      {/* LLM API Key Modal */}
      <TokenInputModal
        isOpen={llmAuthModalModel !== null}
        serverName={llmAuthModalModel || ''}
        onClose={() => setLlmAuthModalModel(null)}
        onUpload={async (tokenData) => {
          await llmAuth.uploadToken(llmAuthModalModel, tokenData)
          setLlmAuthModalModel(null)
        }}
        isLoading={llmAuth.loading}
        error={llmAuth.error}
      />
    </header>
  )
}

export default Header
