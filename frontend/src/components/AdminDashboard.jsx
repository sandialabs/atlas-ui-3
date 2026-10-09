import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft, RefreshCw, Shield, Eye,
  WifiOff, BarChart3
} from 'lucide-react'
import AdminModal from './AdminModal'
import Notifications from './Notifications'
import BannerMessagesCard from './admin/BannerMessagesCard'
import MCPConfigurationCard from './admin/MCPConfigurationCard'
import ConfigViewerCard from './admin/ConfigViewerCard'
import MCPServerManager from './admin/MCPServerManager'
import FeedbackViewerCard from './admin/FeedbackViewerCard'
import HelpConfigCard from './admin/HelpConfigCard'
import { useWS } from '../contexts/WSContext'
import useAdminConfigActions from '../hooks/useAdminConfigActions'

const AdminDashboard = () => {
  const navigate = useNavigate()
  const { isConnected } = useWS()
  const [currentUser, setCurrentUser] = useState('Loading...')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Notifications, system status, and the edit-config modal are shared with the
  // admin tab of the Tools and Settings panel (issue #836).
  const {
    notifications,
    addNotification,
    removeNotification,
    systemStatus,
    loadSystemStatus,
    modalOpen,
    modalData,
    openModal,
    closeModal,
    saveConfig,
    downloadLogs,
  } = useAdminConfigActions()

  const loadDashboard = useCallback(async () => {
    try {
      // Check admin access
      const response = await fetch('/admin/')
      if (!response.ok) {
        if (response.status === 403) {
          setError('Access Denied: You need admin privileges to access this page.')
          return
        }
        throw new Error(`HTTP ${response.status}`)
      }
      
      const data = await response.json()
      setCurrentUser(data.user)
      
      // Load system status
      await loadSystemStatus()
      setLoading(false)
      
    } catch (err) {
      console.error('Error loading dashboard:', err)
      setError('Error loading admin dashboard: ' + err.message)
      setLoading(false)
    }
  }, [loadSystemStatus])

  useEffect(() => {
    loadDashboard()
  }, [loadDashboard])

  const getStatusColor = (status) => {
    switch (status) {
      case 'healthy': return 'text-green-400 bg-green-900/20'
      case 'warning': return 'text-yellow-400 bg-yellow-900/20'
      case 'error': return 'text-red-400 bg-red-900/20'
      default: return 'text-gray-400 bg-gray-800'
    }
  }

  if (loading) {
    return (
      <div className="min-h-full bg-gray-900 text-gray-200 flex items-center justify-center">
        <div className="text-center">
          <RefreshCw className="w-8 h-8 animate-spin mx-auto mb-4" />
          <p>Loading admin dashboard...</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="min-h-full bg-gray-900 text-gray-200 flex items-center justify-center">
        <div className="text-center max-w-md">
          <Shield className="w-16 h-16 text-red-400 mx-auto mb-4" />
          <h2 className="text-xl font-bold mb-2">Access Denied</h2>
          <p className="text-gray-400 mb-6">{error}</p>
          <button 
            onClick={() => navigate('/')}
            className="flex items-center gap-2 mx-auto px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Chat
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-full bg-gray-900 text-gray-200 overflow-y-auto">
      <div className="w-full mx-auto p-6">
        {/* Header */}
        <div className="bg-gray-800 rounded-lg p-6 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h1 className="text-2xl font-bold">ATLAS Admin Dashboard</h1>
            <button 
              onClick={() => navigate('/')}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors"
            >
              <ArrowLeft className="w-4 h-4" />
              Back to Chat
            </button>
          </div>
          <p className="text-gray-400">Logged in as: {currentUser}</p>
        </div>

        {/* Backend disconnected banner */}
        {!isConnected && (
          <div className="flex items-center gap-3 px-4 py-3 mb-6 bg-red-900/30 border border-red-600/40 rounded-lg text-red-300">
            <WifiOff className="w-5 h-5 flex-shrink-0" />
            <div>
              <span className="font-medium">Backend disconnected</span>
              <span className="text-red-400 ml-2 text-sm">Dashboard data may be stale. The connection will retry automatically.</span>
            </div>
          </div>
        )}

        {/* Dashboard Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {/* Banner Messages */}
          <BannerMessagesCard 
            openModal={openModal} 
            addNotification={addNotification} 
          />

          {/* Configuration Viewer */}
          <ConfigViewerCard 
            addNotification={addNotification} 
          />

          {/* System Logs */}
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-4">
              <Eye className="w-6 h-6 text-cyan-400" />
              <h2 className="text-lg font-semibold">System Logs</h2>
            </div>
            <p className="text-gray-400 mb-4">View application logs with enhanced filtering and better UX.</p>
            <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor('healthy')}`}>
              Ready
            </div>
            <button
              onClick={() => navigate('/admin/logview')}
              className="w-full px-4 py-2 bg-cyan-600 hover:bg-cyan-700 rounded-lg transition-colors"
            >
              View Logs
            </button>
          </div>

          {/* Telemetry */}
          <div className="bg-gray-800 rounded-lg p-6">
            <div className="flex items-center gap-3 mb-4">
              <BarChart3 className="w-6 h-6 text-emerald-400" />
              <h2 className="text-lg font-semibold">Telemetry</h2>
            </div>
            <p className="text-gray-400 mb-4">
              OpenTelemetry rollups: tool health, LLM latency, RAG effectiveness, and session drill-down.
            </p>
            <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor('healthy')}`}>
              Ready
            </div>
            <button
              onClick={() => navigate('/admin/telemetry')}
              className="w-full px-4 py-2 bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors"
            >
              View Telemetry
            </button>
          </div>

          {/* MCP Configuration & Controls */}
          <MCPConfigurationCard 
            openModal={openModal} 
            addNotification={addNotification} 
            systemStatus={systemStatus} 
          />

          {/* MCP Server Manager */}
          <MCPServerManager 
            addNotification={addNotification} 
          />

          {/* User Feedback */}
          <FeedbackViewerCard
            openModal={openModal}
            addNotification={addNotification}
          />

          {/* Help Content */}
          <HelpConfigCard
            openModal={openModal}
            addNotification={addNotification}
          />

        </div>
      </div>

      {/* Modal */}
      {modalOpen && <AdminModal 
        data={modalData} 
        onClose={closeModal}
        onSave={saveConfig}
        onDownload={downloadLogs}
        addNotification={addNotification} // Pass addNotification to AdminModal
      />}

      {/* Toast Notifications */}
      <Notifications notifications={notifications} removeNotification={removeNotification} />
    </div>
  )
}

export default AdminDashboard
