/* eslint-disable no-undef */
import { useCallback, useState, useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom' // Import Link
import { ChatProvider, useChat } from './contexts/ChatContext'
import { WSProvider } from './contexts/WSContext'
import { MarketplaceProvider } from './contexts/MarketplaceContext'
import { ThemeProvider } from './contexts/ThemeContext'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'
import SettingsPanel from './components/SettingsPanel'
import RagPanel from './components/RagPanel'
import CanvasPanel from './components/CanvasPanel'
import MarketplacePanel from './components/MarketplacePanel'
import BannerPanel from './components/BannerPanel'
import HelpPage from './components/HelpPage'
import AdminDashboard from './components/AdminDashboard'
import LogViewer from './components/LogViewer' // Import LogViewer
import TelemetryDashboard from './components/TelemetryDashboard'
import FeedbackButton from './components/FeedbackButton'
import FileManagerPanel from './components/FileManagerPanel'
import FilesPage from './components/FilesPage'
import SplashScreen from './components/SplashScreen'
import ElicitationDialog from './components/ElicitationDialog'
import AgentPortal from './components/AgentPortal'
import { ToastProvider, DialogProvider } from './components/ui/ToastProvider'
import { watchAppViewportHeight } from './utils/visualViewportHeight'
import { OPEN_SETTINGS_EVENT } from './utils/settingsPanelEvents'
import { useCanvasLayout } from './hooks/useCanvasLayout'

// Log build info to browser console on startup
console.info(
  `Atlas v${__APP_VERSION__} (${__GIT_HASH__}) | Built ${__BUILD_TIME__}`
)

function ChatInterface() {
  // Tools, prompts, general settings, and admin quick controls are all tabs of
  // one panel now (issue #836); settingsPanelTab picks which one opens.
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false)
  const [settingsPanelTab, setSettingsPanelTab] = useState(null)
  const [promptIntent, setPromptIntent] = useState(null)
  const [ragPanelOpen, setRagPanelOpen] = useState(false)
  const [canvasPanelOpen, setCanvasPanelOpen] = useState(false)
  const [, setCanvasPanelWidth] = useState(0)
  const [filesPanelOpen, setFilesPanelOpen] = useState(false)
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false)
  const { canvasContent, customUIContent, canvasFiles, features, pendingElicitation } = useChat()
  const {
    size: canvasSize,
    effectiveOrientation: canvasOrientation,
    isNarrow: canvasIsNarrow,
    toggleSize: toggleCanvasSize,
    toggleOrientation: toggleCanvasOrientation,
  } = useCanvasLayout()

  // The canvas hides the chat only while it is actually open and set to full size
  const canvasIsFullscreen = canvasPanelOpen && canvasSize === 'full'

  useEffect(() => watchAppViewportHeight(), [])

  const openSettings = useCallback((tab = null, intent = null) => {
    setSettingsPanelTab(tab)
    setPromptIntent(intent)
    setSettingsPanelOpen(true)
  }, [])

  const closeSettings = useCallback(() => {
    setSettingsPanelOpen(false)
    setPromptIntent(null)
  }, [])

  // The prompts tab reports back once an intent has opened its editor; holding
  // it any longer would re-apply it every time that tab is re-entered.
  const clearPromptIntent = useCallback(() => setPromptIntent(null), [])

  // Auto-open the tools tab when returning from the marketplace
  useEffect(() => {
    const shouldOpenToolsPanel = sessionStorage.getItem('openToolsPanel')
    if (shouldOpenToolsPanel === 'true') {
      openSettings('tools')
      sessionStorage.removeItem('openToolsPanel') // Clear the flag
    }
  }, [openSettings])

  // Components too deep to receive props (the prompt selector under the chat
  // box) ask for a specific tab via a window event.
  useEffect(() => {
    const handleOpenRequest = (event) => {
      const { tab = null, promptIntent: intent = null } = event.detail || {}
      openSettings(tab, intent)
    }
    window.addEventListener(OPEN_SETTINGS_EVENT, handleOpenRequest)
    return () => window.removeEventListener(OPEN_SETTINGS_EVENT, handleOpenRequest)
  }, [openSettings])

  // Auto-open canvas panel when content is received
  useEffect(() => {
    if (canvasContent && canvasContent.trim()) {
      // Close other panels when canvas opens. The Tools and Settings modal is
      // left alone on purpose -- closing it would discard unsaved edits.
      setFilesPanelOpen(false)
      setCanvasPanelOpen(true)
    }
  }, [canvasContent])

  // Auto-open canvas panel when custom UI content is received
  useEffect(() => {
    if (customUIContent) {
      // Close other panels when canvas opens. The Tools and Settings modal is
      // left alone on purpose -- closing it would discard unsaved edits.
      setFilesPanelOpen(false)
      setCanvasPanelOpen(true)
    }
  }, [customUIContent])

  // Auto-open canvas panel when viewable files are received
  useEffect(() => {
    if (canvasFiles && canvasFiles.length > 0) {
      // Close other panels when canvas opens. The Tools and Settings modal is
      // left alone on purpose -- closing it would discard unsaved edits.
      setFilesPanelOpen(false)
      setCanvasPanelOpen(true)
    }
  }, [canvasFiles])

  return (
    <div
      className="relative flex flex-col w-full bg-gray-900 text-gray-200 overflow-hidden"
      style={{ height: 'var(--app-viewport-height, 100vh)' }}
    >
      {/* Banner Panel - full width across the top */}
      <BannerPanel />

      {/* Below banner: sidebar + main content side by side */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar - only when chat history is enabled */}
        {features?.chat_history && (
          <Sidebar
            mobileOpen={sidebarMobileOpen}
            onMobileClose={() => setSidebarMobileOpen(false)}
          />
        )}

        {/* RAG Data Sources Panel */}
        {features?.rag && (
          <RagPanel
            isOpen={ragPanelOpen}
            onClose={() => setRagPanelOpen(false)}
          />
        )}

        {/* Main Content Area */}
        <div className="flex flex-col flex-1 min-w-0 relative">
          {/* Header */}
          <Header
            onToggleSidebar={() => setSidebarMobileOpen(!sidebarMobileOpen)}
            onToggleRag={() => setRagPanelOpen(!ragPanelOpen)}
            onToggleFiles={() => {
              if (!filesPanelOpen) {
                setCanvasPanelOpen(false)
              }
              setFilesPanelOpen(!filesPanelOpen)
            }}
            onToggleCanvas={() => {
              if (!canvasPanelOpen) {
                setFilesPanelOpen(false)
              }
              setCanvasPanelOpen(!canvasPanelOpen)
            }}
            onToggleSettings={(tab = null) => {
              if (settingsPanelOpen) {
                closeSettings()
              } else {
                openSettings(tab)
              }
            }}
            onCloseCanvas={() => setCanvasPanelOpen(false)}
          />

          {/* Content Area - chat and canvas, side by side or stacked */}
          <div
            className={`flex flex-1 overflow-hidden min-h-0 ${canvasOrientation === 'top' ? 'flex-col-reverse' : ''}`}
          >
            {/* Chat Area - kept mounted (never unmounted) so drafts survive a full-size canvas */}
            <div
              className={`flex flex-1 min-h-0 min-w-0 overflow-hidden ${canvasIsFullscreen ? 'hidden' : ''}`}
            >
              <ChatArea />
            </div>

            {/* Canvas Panel */}
            <CanvasPanel
              isOpen={canvasPanelOpen}
              onClose={() => setCanvasPanelOpen(false)}
              onWidthChange={setCanvasPanelWidth}
              size={canvasSize}
              orientation={canvasOrientation}
              orientationLocked={canvasIsNarrow}
              onToggleSize={toggleCanvasSize}
              onToggleOrientation={toggleCanvasOrientation}
            />
          </div>
        </div>

        {/* Combined Tools and Settings Panel */}
        <SettingsPanel
          isOpen={settingsPanelOpen}
          onClose={closeSettings}
          initialTab={settingsPanelTab}
          promptIntent={promptIntent}
          onPromptIntentConsumed={clearPromptIntent}
        />

        {/* Right Side Panels Container */}
        <div className="relative flex-shrink-0">
          {/* File Manager Panel */}
          {features?.files_panel && (
            <FileManagerPanel
              isOpen={filesPanelOpen}
              onClose={() => setFilesPanelOpen(false)}
            />
          )}
        </div>
      </div>

      {/* Feedback Button */}
      <FeedbackButton />

      {/* Elicitation Dialog */}
      {pendingElicitation && (
        <ElicitationDialog elicitation={pendingElicitation} />
      )}
    </div>
  )
}

function AppRoutes() {
  const { features } = useChat()

  return (
    <Routes>
      <Route path="/" element={<ChatInterface />} />
      {features?.marketplace && <Route path="/marketplace" element={<MarketplacePanel />} />}
      <Route path="/help" element={<HelpPage />} />
      <Route path="/admin" element={<AdminDashboard />} />
      <Route path="/files" element={<FilesPage />} />
      <Route path="/admin/logview" element={<LogViewer />} /> {/* New route for LogViewer */}
      <Route path="/admin/telemetry" element={<TelemetryDashboard />} />
      {features?.agent_portal && <Route path="/agent-portal" element={<AgentPortal />} />}
    </Routes>
  )
}

function App() {
  const [splashConfig, setSplashConfig] = useState(null)
  
  // Fetch splash screen configuration on app load
  useEffect(() => {
    const fetchSplashConfig = async () => {
      try {
        const response = await fetch('/api/splash')
        if (response.ok) {
          const config = await response.json()
          setSplashConfig(config)
        } else {
          console.warn('Failed to fetch splash configuration')
          setSplashConfig({ enabled: false })
        }
      } catch (error) {
        console.error('Error fetching splash configuration:', error)
        setSplashConfig({ enabled: false })
      }
    }
    
    fetchSplashConfig()
  }, [])
  
  return (
    <ThemeProvider>
      <Router>
        <ToastProvider>
          <DialogProvider>
            <WSProvider>
              <ChatProvider>
                <MarketplaceProvider>
                  <SplashScreen config={splashConfig} />
                  <AppRoutes />
                </MarketplaceProvider>
              </ChatProvider>
            </WSProvider>
          </DialogProvider>
        </ToastProvider>
      </Router>
    </ThemeProvider>
  )
}

export default App
