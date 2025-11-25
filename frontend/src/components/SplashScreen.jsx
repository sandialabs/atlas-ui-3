import { useState, useEffect } from 'react'
import { X, CheckCircle } from 'lucide-react'

/**
 * SplashScreen component that displays important policies and information.
 * 
 * Features:
 * - Displays configurable messages (text and headings)
 * - Can be dismissed or require acceptance
 * - Tracks dismissal using localStorage for N days
 * - Fully configurable via backend API
 */
const SplashScreen = ({ config, onClose }) => {
  const [isVisible, setIsVisible] = useState(false)
  
  useEffect(() => {
    // Don't show if not enabled
    if (!config || !config.enabled) {
      return
    }

    // Check if already dismissed
    const dismissKey = 'splash-screen-dismissed'
    const dismissedData = localStorage.getItem(dismissKey)
    
    if (dismissedData && !config.show_on_every_visit) {
      try {
        const { timestamp } = JSON.parse(dismissedData)
        const dismissedDate = new Date(timestamp)
        const now = new Date()
        const daysSinceDismiss = (now - dismissedDate) / (1000 * 60 * 60 * 24)
        
        // If within the dismiss duration, don't show
        if (daysSinceDismiss < config.dismiss_duration_days) {
          return
        }
      } catch (e) {
        // Invalid data, show splash screen
        console.warn('Invalid splash screen dismissal data:', e)
      }
    }
    
    // Show splash screen
    setIsVisible(true)
  }, [config])
  
  const handleDismiss = () => {
    if (config.dismissible) {
      // Save dismissal to localStorage
      const dismissKey = 'splash-screen-dismissed'
      const dismissalData = {
        timestamp: new Date().toISOString(),
        version: config.title || 'default'
      }
      localStorage.setItem(dismissKey, JSON.stringify(dismissalData))
      
      setIsVisible(false)
      if (onClose) {
        onClose()
      }
    }
  }
  
  const handleAccept = () => {
    // Save acceptance to localStorage
    const dismissKey = 'splash-screen-dismissed'
    const dismissalData = {
      timestamp: new Date().toISOString(),
      version: config.title || 'default',
      accepted: true
    }
    localStorage.setItem(dismissKey, JSON.stringify(dismissalData))
    
    setIsVisible(false)
    if (onClose) {
      onClose()
    }
  }
  
  if (!isVisible || !config || !config.enabled) {
    return null
  }
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-75">
      <div className="bg-gray-800 rounded-lg shadow-2xl max-w-2xl w-full mx-4 max-h-[80vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-700">
          <h2 className="text-xl font-semibold text-gray-100">
            {config.title || 'Welcome'}
          </h2>
          {config.dismissible && !config.require_accept && (
            <button
              onClick={handleDismiss}
              className="text-gray-400 hover:text-gray-200 transition-colors"
              aria-label="Close"
            >
              <X className="w-6 h-6" />
            </button>
          )}
        </div>
        
        {/* Content */}
        <div className="px-6 py-4 overflow-y-auto flex-1">
          <div className="space-y-4 text-gray-300">
            {config.messages && config.messages.map((message, index) => {
              if (message.type === 'heading') {
                return (
                  <h3 key={index} className="text-lg font-semibold text-gray-100 mt-4 first:mt-0">
                    {message.content}
                  </h3>
                )
              } else if (message.type === 'text') {
                return (
                  <p key={index} className="text-sm leading-relaxed">
                    {message.content}
                  </p>
                )
              }
              return null
            })}
          </div>
        </div>
        
        {/* Footer */}
        <div className="flex items-center justify-end gap-3 px-6 py-4 border-t border-gray-700">
          {config.require_accept ? (
            <button
              onClick={handleAccept}
              className="flex items-center gap-2 px-6 py-2 bg-cyan-600 hover:bg-cyan-700 text-white rounded-lg transition-colors font-medium"
            >
              <CheckCircle className="w-5 h-5" />
              {config.accept_button_text || 'Accept'}
            </button>
          ) : config.dismissible ? (
            <button
              onClick={handleDismiss}
              className="px-6 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded-lg transition-colors font-medium"
            >
              {config.dismiss_button_text || 'Close'}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default SplashScreen
