import { useState } from 'react'
import { X } from 'lucide-react'
import { usePollingWithBackoff } from '../hooks/usePollingWithBackoff'

const BANNER_POLL_INTERVAL = 5 * 60 * 1000 // 5 minutes
const BANNER_MAX_BACKOFF = 30 * 60 * 1000 // 30 minutes max backoff

function BannerPanel() {
  const [bannerMessages, setBannerMessages] = useState([])
  const [dismissedMessages, setDismissedMessages] = useState(new Set())
  const [bannerEnabled, setBannerEnabled] = useState(false)

  usePollingWithBackoff(
    async () => {
      const configResponse = await fetch('/api/config')
      if (!configResponse.ok) throw new Error(`HTTP ${configResponse.status}`)
      const config = await configResponse.json()

      if (!config.banner_enabled) {
        setBannerEnabled(false)
        return
      }

      setBannerEnabled(true)

      const bannersResponse = await fetch('/api/banners')
      if (!bannersResponse.ok) throw new Error(`HTTP ${bannersResponse.status}`)
      const bannersData = await bannersResponse.json()
      setBannerMessages(bannersData.messages || [])
    },
    {
      normalInterval: BANNER_POLL_INTERVAL,
      maxBackoffDelay: BANNER_MAX_BACKOFF,
    }
  )

  const handleDismiss = (index) => {
    setDismissedMessages(prev => new Set([...prev, index]))
  }

  // Don't render anything if banners are disabled or no messages
  if (!bannerEnabled || bannerMessages.length === 0) {
    return null
  }

  // Filter out dismissed messages
  const visibleMessages = bannerMessages.filter((_, index) => !dismissedMessages.has(index))

  if (visibleMessages.length === 0) {
    return null
  }

  return (
    <div className="w-full bg-gradient-to-r from-yellow-500 to-orange-500 text-white">
      {visibleMessages.map((message) => {
        // Find the original index in the full array
        const actualIndex = bannerMessages.findIndex((msg, idx) => 
          msg === message && !dismissedMessages.has(idx)
        )
        
        return (
          <div
            key={actualIndex}
            className="flex items-center justify-between px-4 py-2 border-b border-yellow-400 last:border-b-0"
          >
            <div className="flex-1 text-sm font-medium">
              {message}
            </div>
            <button
              onClick={() => handleDismiss(actualIndex)}
              className="ml-4 p-1 hover:bg-yellow-600 hover:bg-opacity-30 rounded-full transition-colors"
              aria-label="Dismiss banner"
            >
              <X size={16} />
            </button>
          </div>
        )
      })}
    </div>
  )
}

export default BannerPanel
