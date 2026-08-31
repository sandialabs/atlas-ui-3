import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useChat } from '../contexts/ChatContext'
import { useWS } from '../contexts/WSContext'
import { useMarketplace } from '../contexts/MarketplaceContext'
import WorkspaceSelector from './WorkspaceSelector'
import { Database, Wrench, Bot, Download, Plus, CircleHelp, Shield, FolderOpen, Monitor, Menu, X, PanelLeft, HardDrive, Cloud, Printer, Terminal } from 'lucide-react'
import { nextSaveMode } from '../utils/saveModeConfig'
import { useElementWidth } from '../hooks/useElementWidth'
import { useToast } from './ui/toastContext'

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

// Header width (not viewport width) at which the full desktop button cluster
// fits. Below it the cluster collapses into the hamburger menu. The cluster is
// smaller since issue #839 -- the model picker moved to the chat bar and the
// admin shield, Help label, and Portal label are gone -- so the threshold came
// down with it, and still carries headroom for locale-dependent label widths.
const DESKTOP_ACTIONS_MIN_WIDTH = 1080

// Below this the left-hand buttons drop their text labels and show icons only.
// This is the header's own width rather than a Tailwind viewport breakpoint
// (`hidden md:inline`): the header sits beside a sidebar and a canvas panel, so
// a viewport query kept labels at widths where they no longer fit and dropped
// them at widths where they did (issue #839 review -- "New chat has this
// problem in particular").
const ACTION_LABELS_MIN_WIDTH = 760

const Header = ({ onToggleSidebar, onToggleRag, onToggleFiles, onToggleCanvas, onCloseCanvas, onToggleSettings }) => {
  const navigate = useNavigate()
  const {
    user,
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
    complianceLevelFilter,
    setComplianceLevelFilter,
    selectedDataSources
  } = useChat()
  const { complianceLevels } = useMarketplace()
  const { connectionStatus, isConnected } = useWS()
  const toast = useToast()
  const [downloadDropdownOpen, setDownloadDropdownOpen] = useState(false)
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)
  // The desktop cluster is gated on the header's own width, not the viewport's:
  // the header sits beside a 256px sidebar, so a viewport query would reveal the
  // cluster while the header still lacks room for it. See useElementWidth.
  const [headerRef, headerWidth] = useElementWidth()
  const showDesktopActions = headerWidth >= DESKTOP_ACTIONS_MIN_WIDTH
  const showActionLabels = headerWidth >= ACTION_LABELS_MIN_WIDTH
  
  // Extract unique compliance levels from all available tools and prompts
  const availableComplianceLevels = complianceLevels.map(l => l.name)

  // Reset the compact menu once the header is wide enough for the desktop
  // cluster, so it does not spring back open if the header narrows again. The
  // render above is already gated on !showDesktopActions, so this is state
  // hygiene rather than what actually hides the menu.
  useEffect(() => {
    if (showDesktopActions) setMobileMenuOpen(false)
  }, [showDesktopActions])

  // Close dropdowns when mobile menu opens
  useEffect(() => {
    if (mobileMenuOpen) {
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
        // Gate canvas-close + focus on a successful clear so a cancelled
        // confirm doesn't still slam the canvas shut or steal focus.
        if (clearChat() === false) return
        onCloseCanvas()
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

  // Handle hotkey for toggling agent mode (Ctrl+Alt+A). Mirrors the Ctrl+Alt+N
  // new-chat shortcut. Ctrl+Alt avoids the Ctrl+A "select all" collision, and
  // works even while the message input is focused (capture phase + preventDefault).
  useEffect(() => {
    if (!agentModeAvailable) return
    const handleKeyDown = (event) => {
      if (event.ctrlKey && event.altKey && (event.key === 'A' || event.key === 'a')) {
        event.preventDefault()
        event.stopPropagation()
        const next = !agentModeEnabled
        setAgentModeEnabled(next)
        toast.info(`Agent Mode ${next ? 'enabled' : 'disabled'}`)
      }
    }
    document.addEventListener('keydown', handleKeyDown, true) // Use capture phase
    return () => document.removeEventListener('keydown', handleKeyDown, true)
  }, [agentModeAvailable, agentModeEnabled, setAgentModeEnabled, toast])

  return (
    <header ref={headerRef} className="flex items-center justify-between p-2 sm:p-4 bg-gray-800 border-b border-gray-700">
      {/* Left section. Deliberately not min-w-0: these are fixed-size icon
          buttons with nothing to truncate, so letting the section shrink below
          them only makes it paint over the right-hand group. The right section
          shrinks instead, truncating the model name. */}
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
            {showActionLabels && (
              <span className="text-sm font-medium whitespace-nowrap">
                {selectedDataSources?.size > 0 ? `${selectedDataSources.size} sources` : 'Sources'}
              </span>
            )}
          </button>
        )}
        
        {/* Workspace switcher: prompt + data sources + tools as one context */}
        {features?.workspaces && <WorkspaceSelector />}

        {/* New Chat Button */}
        <button
          onClick={() => {
            if (clearChat() === false) return
            onCloseCanvas()
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
          {showActionLabels && (
            <span className="text-sm font-medium whitespace-nowrap">New Chat</span>
          )}
        </button>
      </div>

      {/* Right section */}
      <div className="flex items-center gap-2 sm:gap-4 min-w-0">
        {/* The model picker moved to the chat bar (issue #839 review): the
            message box is where people work, so the model in use belongs there.
            See ModelSelector, rendered by ChatArea. */}

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
              <span className="hidden sm:inline whitespace-nowrap">{cfg.label}</span>
            </button>
          )
        })()}

        {/* Desktop-only buttons (hidden on mobile, shown in hamburger menu) */}
        <div className={`${showDesktopActions ? 'flex' : 'hidden'} items-center gap-2`}>
          {/* User Info */}
          <div className="text-sm text-gray-300 min-w-0 max-w-[12rem] truncate" title={user}>
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
                  className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700"
                >
                  Download as Text
                </button>
                <button
                  onClick={() => {
                    window.print()
                    setDownloadDropdownOpen(false)
                  }}
                  className="w-full text-left px-4 py-2 text-sm text-gray-200 hover:bg-gray-700 last:rounded-b-lg flex items-center gap-2"
                >
                  <Printer className="w-4 h-4" />
                  Print / Save as PDF
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
              title={agentModeEnabled ? "Agent Mode: ON (click or Ctrl+Alt+A to disable)" : "Agent Mode: OFF (click or Ctrl+Alt+A to enable)"}
            >
              <Bot className="w-5 h-5" />
            </button>
          )}

          {/* The admin shield is gone from the toolbar (issue #839 review):
              admin controls are the Admin tab of Tools and Settings, and the
              full dashboard is one click further in from there. */}

          {/* Tools and Settings -- tools, integrations, prompts, general
              settings (including light/dark), and admin quick controls all
              live behind this one button (issue #836). */}
          <button
            onClick={() => onToggleSettings()}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Tools and Settings"
            aria-label="Tools and Settings"
          >
            <Wrench className="w-5 h-5" />
          </button>

          {/* Help. Icon only, and a ringed question mark rather than the old
              outline circle: the reviewer asked for a clearer help glyph so the
              word could go and the toolbar could lose a chunk of text. */}
          <button
            onClick={() => navigate('/help')}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors text-blue-300"
            title="Help"
            aria-label="Help"
          >
            <CircleHelp className="w-5 h-5" />
          </button>

          {/* Agent Portal Button */}
          {features?.agent_portal && (
            <button
              onClick={() => navigate('/agent-portal')}
              className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
              title="Agent Portal -- launch host processes"
              aria-label="Agent Portal"
            >
              <Terminal className="w-5 h-5" />
            </button>
          )}

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
          className={`${showDesktopActions ? 'hidden' : 'block'} p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors`}
          title="Menu"
          aria-expanded={mobileMenuOpen}
          aria-controls="mobile-menu"
          aria-label={mobileMenuOpen ? "Close menu" : "Open menu"}
        >
          {mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {/* Mobile Menu Overlay. Gated on the render, not just on the effect below:
          an effect runs after paint, so keying this on state alone would flash
          the backdrop over the desktop cluster for a frame -- and let it swallow
          a click -- while the header is being widened past the threshold. */}
      {mobileMenuOpen && !showDesktopActions && (
        <>
          {/* Backdrop */}
          <div 
            className="fixed inset-0 bg-black bg-opacity-50 z-40" 
            onClick={() => setMobileMenuOpen(false)}
          />
          
          {/* Menu Panel */}
          <div id="mobile-menu" className="fixed top-[57px] sm:top-[65px] right-0 w-64 bg-gray-800 border-l border-gray-700 shadow-lg z-50 max-h-[calc(100vh-57px)] sm:max-h-[calc(100vh-65px)] overflow-y-auto">
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

              <button
                onClick={() => {
                  window.print()
                  setMobileMenuOpen(false)
                }}
                disabled={messages.length === 0}
                className={`w-full flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors ${
                  messages.length === 0
                    ? 'bg-gray-700 text-gray-500 cursor-not-allowed'
                    : 'bg-gray-700 hover:bg-gray-600 text-gray-200'
                }`}
              >
                <Printer className="w-5 h-5" />
                <span>Print / Save as PDF</span>
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
                  <span className="ml-auto text-xs opacity-60">Ctrl+Alt+A</span>
                </button>
              )}

              {/* Tools and Settings (issue #836) */}
              <button
                onClick={() => {
                  onToggleSettings()
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <Wrench className="w-5 h-5" />
                <span>Tools and Settings</span>
              </button>

              {/* Help Button */}
              <button
                onClick={() => {
                  navigate('/help')
                  setMobileMenuOpen(false)
                }}
                className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
              >
                <CircleHelp className="w-5 h-5" />
                <span>Help</span>
              </button>

              {/* Agent Portal */}
              {features?.agent_portal && (
                <button
                  onClick={() => {
                    navigate('/agent-portal')
                    setMobileMenuOpen(false)
                  }}
                  className="w-full flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-700 hover:bg-gray-600 text-sm transition-colors"
                >
                  <Terminal className="w-5 h-5" />
                  <span>Agent Portal</span>
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
