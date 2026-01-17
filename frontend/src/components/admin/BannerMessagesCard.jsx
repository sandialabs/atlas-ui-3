import React, { useState, useEffect } from 'react'
import { MessageSquare, AlertCircle } from 'lucide-react'

const BannerMessagesCard = ({ openModal, addNotification }) => {
  const [bannerEnabled, setBannerEnabled] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchBannerStatus = async () => {
      try {
        const response = await fetch('/admin/banners')
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        const data = await response.json()
        setBannerEnabled(data.banner_enabled)
      } catch (err) {
        console.error('Error fetching banner status:', err)
        addNotification('Error loading banner status: ' + err.message, 'error')
      } finally {
        setLoading(false)
      }
    }
    fetchBannerStatus()
  }, [addNotification])

  const manageBanners = async () => {
    try {
      const response = await fetch('/admin/banners')
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json()
      
      openModal('Manage Banner Messages', {
        type: 'textarea',
        value: data.messages.join('\n'),
        description: 'These messages will be displayed at the top of the chat interface.'
      }, 'banners')
    } catch (err) {
      addNotification('Error loading banner configuration: ' + err.message, 'error')
    }
  }

  const getStatusColor = (status) => {
    switch (status) {
      case 'healthy': return 'text-green-400 bg-green-900/20'
      case 'warning': return 'text-yellow-400 bg-yellow-900/20'
      case 'error': return 'text-red-400 bg-red-900/20'
      default: return 'text-gray-400 bg-gray-800'
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg p-6">
      <div className="flex items-center gap-3 mb-4">
        <MessageSquare className="w-6 h-6 text-blue-400" />
        <h2 className="text-lg font-semibold">Banner Messages</h2>
      </div>
      <p className="text-gray-400 mb-4">Manage messages displayed at the top of the chat interface.</p>
      
      {!loading && !bannerEnabled && (
        <div className="flex items-start gap-2 px-3 py-2 mb-4 bg-yellow-900/20 border border-yellow-600/30 rounded text-sm text-yellow-400">
          <AlertCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>Banner feature is currently disabled. Enable BANNER_ENABLED in configuration to use this feature.</span>
        </div>
      )}
      
      <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${bannerEnabled ? getStatusColor('healthy') : getStatusColor('warning')}`}>
        {bannerEnabled ? 'Ready' : 'Feature Disabled'}
      </div>
      <button 
        onClick={manageBanners}
        disabled={!bannerEnabled || loading}
        className={`w-full px-4 py-2 rounded-lg transition-colors ${
          bannerEnabled && !loading
            ? 'bg-blue-600 hover:bg-blue-700 cursor-pointer'
            : 'bg-gray-600 cursor-not-allowed opacity-50'
        }`}
      >
        Manage Banners
      </button>
    </div>
  )
}

export default BannerMessagesCard
