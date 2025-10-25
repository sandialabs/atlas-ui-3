import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { Menu, ChevronDown, Wrench, Bot, Download, Plus, HelpCircle, Shield, FolderOpen, Monitor, Settings } from 'lucide-react'

const Header = ({ onToggleRag, onToggleTools, onToggleFiles, onToggleCanvas, onCloseCanvas, onToggleSettings }) => {
  const navigate = useNavigate()
  const { 
    user, 
    models, 
    currentModel, 
    setCurrentModel, 
    agentModeAvailable,
    agentModeEnabled,
    setAgentModeEnabled,
    downloadChat,
    downloadChatAsText,
    messages,
    clearChat,
    features,
    isInAdminGroup
  } = useChat()
  const { connectionStatus, isConnected } = useWS()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [downloadDropdownOpen, setDownloadDropdownOpen] = useState(false)

  const handleModelSelect = (model) => {
    setCurrentModel(model)
    setDropdownOpen(false)
  }

  // Handle hotkey for new chat (Ctrl+Alt+N)
  useEffect(() => {
    const handleKeyDown = (event) => {
      // Debug logging
      if (event.ctrlKey && event.altKey) {
        console.log('Ctrl+Alt pressed with key:', event.key, event.code)
      }
      
      if (event.ctrlKey && event.altKey && (event.key === 'N' || event.key === 'n')) {
        event.preventDefault()
        event.stopPropagation()
        console.log('New chat hotkey triggered!')
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
  }, [clearChat])

  return (
    <header className="flex items-center justify-between p-4 bg-gray-800 border-b border-gray-700">
      {/* Left section */}
      <div className="flex items-center gap-4">
        {features?.rag && (
          <button
            onClick={onToggleRag}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Toggle Data Sources"
          >
            <Menu className="w-5 h-5" />
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
          className="flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-gray-200"
          title="Start New Chat (Ctrl+Alt+N)"
        >
          <Plus className="w-4 h-4" />
          <span className="text-sm font-medium hidden sm:inline">New Chat</span>
        </button>
      </div>

      {/* Right section */}
      <div className="flex items-center gap-4">
        {/* Model Selection Dropdown */}
        <div className="relative">
          <button
            onClick={() => setDropdownOpen(!dropdownOpen)}
            className="flex items-center gap-2 px-4 py-2 bg-gray-700 rounded-lg hover:bg-gray-600 transition-colors min-w-40"
          >
            <span className="text-sm text-gray-200">
              {currentModel || 'Select a model...'}
            </span>
            <ChevronDown className="w-4 h-4" />
          </button>
          
          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-1 w-64 bg-gray-800 border border-gray-600 rounded-lg shadow-lg z-50">
              {models.length === 0 ? (
                <div className="px-4 py-2 text-gray-400 text-sm">No models available</div>
              ) : (
                models.map(model => (
                  <button
                    key={model}
                    onClick={() => handleModelSelect(model)}
                    className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 first:rounded-t-lg last:rounded-b-lg"
                  >
                    {model}
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        {/* User Info */}
        <div className="text-sm text-gray-300 hidden md:block">
          {user}
        </div>

        {/* Connection Status */}
        <div className="flex items-center gap-2 text-xs">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-gray-400 hidden sm:inline">{connectionStatus}</span>
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
            title="Toggle Tools"
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
    </header>
  )
}

export default Header
