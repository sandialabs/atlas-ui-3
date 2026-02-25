import { useState, useEffect } from 'react'
import { BrowserRouter as Router, Routes, Route, Link } from 'react-router-dom' // Import Link
import { ChatProvider, useChat } from './contexts/ChatContext'
import { WSProvider } from './contexts/WSContext'
import { MarketplaceProvider } from './contexts/MarketplaceContext'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import ChatArea from './components/ChatArea'
import ToolsPanel from './components/ToolsPanel'
import SettingsPanel from './components/SettingsPanel'
import RagPanel from './components/RagPanel'
import CanvasPanel from './components/CanvasPanel'
import MarketplacePanel from './components/MarketplacePanel'
import BannerPanel from './components/BannerPanel'
import HelpPage from './components/HelpPage'
import AdminDashboard from './components/AdminDashboard'
import LogViewer from './components/LogViewer' // Import LogViewer
import FeedbackButton from './components/FeedbackButton'
import FileManagerPanel from './components/FileManagerPanel'
import FilesPage from './components/FilesPage'
import SplashScreen from './components/SplashScreen'
import ElicitationDialog from './components/ElicitationDialog'

function ChatInterface() {
  const [toolsPanelOpen, setToolsPanelOpen] = useState(false)
  const [settingsPanelOpen, setSettingsPanelOpen] = useState(false)
  const [ragPanelOpen, setRagPanelOpen] = useState(false)
  const [canvasPanelOpen, setCanvasPanelOpen] = useState(false)
  const [, setCanvasPanelWidth] = useState(0)
  const [filesPanelOpen, setFilesPanelOpen] = useState(false)
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false)
  const { canvasContent, customUIContent, canvasFiles, features, pendingElicitation } = useChat()

  // Auto-open tools panel when returning from marketplace
  useEffect(() => {
    const shouldOpenToolsPanel = sessionStorage.getItem('openToolsPanel')
    if (shouldOpenToolsPanel === 'true') {
      setToolsPanelOpen(true)
      sessionStorage.removeItem('openToolsPanel') // Clear the flag
    }
  }, [])

  // Auto-open canvas panel when content is received
  useEffect(() => {
    if (canvasContent && canvasContent.trim()) {
      // Close other panels when canvas opens
      setToolsPanelOpen(false)
      setFilesPanelOpen(false)
      setCanvasPanelOpen(true)
    }
  }, [canvasContent])

  // Auto-open canvas panel when custom UI content is received
  useEffect(() => {
    if (customUIContent) {
      // Close other panels when canvas opens
      setToolsPanelOpen(false)
      setFilesPanelOpen(false)
      setCanvasPanelOpen(true)
    }
  }, [customUIContent])

  // Auto-open canvas panel when viewable files are received
  useEffect(() => {
    if (canvasFiles && canvasFiles.length > 0) {
      // Close other panels when canvas opens
      setToolsPanelOpen(false)
      setFilesPanelOpen(false)
      setCanvasPanelOpen(true)
    }
  }, [canvasFiles])

  return (
    <div className="flex flex-col h-screen w-full bg-gray-900 text-gray-200 overflow-hidden">
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
            onToggleTools={() => {
              if (!toolsPanelOpen) {
                setCanvasPanelOpen(false)
                setFilesPanelOpen(false)
              }
              setToolsPanelOpen(!toolsPanelOpen)
            }}
            onToggleFiles={() => {
              if (!filesPanelOpen) {
                setCanvasPanelOpen(false)
                setToolsPanelOpen(false)
              }
              setFilesPanelOpen(!filesPanelOpen)
            }}
            onToggleCanvas={() => {
              if (!canvasPanelOpen) {
                setToolsPanelOpen(false)
                setFilesPanelOpen(false)
              }
              setCanvasPanelOpen(!canvasPanelOpen)
            }}
            onToggleSettings={() => setSettingsPanelOpen(!settingsPanelOpen)}
            onCloseCanvas={() => setCanvasPanelOpen(false)}
          />

          {/* Content Area - Chat and Canvas side by side */}
          <div className="flex flex-1 overflow-hidden min-h-0">
            {/* Chat Area */}
            <ChatArea />

            {/* Canvas Panel */}
            <CanvasPanel
              isOpen={canvasPanelOpen}
              onClose={() => setCanvasPanelOpen(false)}
              onWidthChange={setCanvasPanelWidth}
            />
          </div>
        </div>

        {/* Tools Panel Overlay */}
        {features?.tools && (
          <ToolsPanel
            isOpen={toolsPanelOpen}
            onClose={() => setToolsPanelOpen(false)}
          />
        )}

        {/* Settings Panel Overlay */}
        <SettingsPanel
          isOpen={settingsPanelOpen}
          onClose={() => setSettingsPanelOpen(false)}
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
    <Router>
      <WSProvider>
        <ChatProvider>
          <MarketplaceProvider>
            <SplashScreen config={splashConfig} />
            <AppRoutes />
          </MarketplaceProvider>
        </ChatProvider>
      </WSProvider>
    </Router>
  )
}

export default App
