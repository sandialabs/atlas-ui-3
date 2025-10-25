import React from 'react'
import { X } from 'lucide-react'

const Notifications = ({ notifications, removeNotification }) => {
  return (
    <div className="fixed top-4 right-4 z-50 space-y-2">
      {notifications.map((notification) => (
        <div
          key={notification.id}
          className={`max-w-sm p-4 rounded-lg shadow-lg transition-all duration-300 ${
            notification.type === 'error' ? 'bg-red-600 text-white' :
            notification.type === 'success' ? 'bg-green-600 text-white' :
            'bg-blue-600 text-white'
          }`}
        >
          <div className="flex items-center justify-between">
            <p className="text-sm font-medium">{notification.message}</p>
            <button
              onClick={() => removeNotification(notification.id)}
              className="ml-2 text-white hover:text-gray-200 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      ))}
    </div>
  )
}

export default Notifications
