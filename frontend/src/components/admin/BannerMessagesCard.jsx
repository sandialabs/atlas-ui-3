import React from 'react'
import { MessageSquare } from 'lucide-react'

const BannerMessagesCard = ({ openModal, addNotification }) => {
  const manageBanners = async () => {
    try {
      const response = await fetch('/admin/banners')
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
      <div className={`px-3 py-1 rounded text-sm font-medium mb-4 ${getStatusColor('healthy')}`}>
        Ready
      </div>
      <button 
        onClick={manageBanners}
        className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors"
      >
        Manage Banners
      </button>
    </div>
  )
}

export default BannerMessagesCard
