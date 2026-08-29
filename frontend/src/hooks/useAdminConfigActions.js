import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Shared admin plumbing: toast notifications, system status, and the
 * edit-config modal wiring used by the admin cards.
 *
 * Both the full admin dashboard and the compact admin tab inside the combined
 * "Tools and Settings" panel (issue #836) drive the same cards, so the state
 * those cards need (`openModal`, `addNotification`, `systemStatus`) lives here
 * instead of being duplicated per host.
 */
export function useAdminConfigActions() {
  const [notifications, setNotifications] = useState([])
  const [systemStatus, setSystemStatus] = useState({})
  const [modalOpen, setModalOpen] = useState(false)
  const [modalData, setModalData] = useState({})
  const currentEndpointRef = useRef(null)
  // Pending auto-dismiss timers, cleared on unmount so they never fire against
  // a hook that is gone (the admin surfaces mount and unmount freely).
  const dismissTimersRef = useRef(new Map())

  useEffect(() => {
    const timers = dismissTimersRef.current
    return () => {
      timers.forEach(timer => clearTimeout(timer))
      timers.clear()
    }
  }, [])

  const removeNotification = useCallback((id) => {
    const timer = dismissTimersRef.current.get(id)
    if (timer) {
      clearTimeout(timer)
      dismissTimersRef.current.delete(id)
    }
    setNotifications(prev => prev.filter(n => n.id !== id))
  }, [])

  const addNotification = useCallback((message, type = 'info') => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    setNotifications(prev => [...prev, { id, message, type }])

    // Auto-remove after 5 seconds for success/info, 8 seconds for errors
    const timer = setTimeout(() => {
      dismissTimersRef.current.delete(id)
      setNotifications(prev => prev.filter(n => n.id !== id))
    }, type === 'error' ? 8000 : 5000)
    dismissTimersRef.current.set(id, timer)
  }, [])

  const loadSystemStatus = useCallback(async () => {
    try {
      const response = await fetch('/admin/system-status')
      const data = await response.json()
      setSystemStatus(data)
    } catch (err) {
      console.error('Error loading system status:', err)
    }
  }, [])

  const openModal = useCallback((title, content, endpoint = null, contentCategory = null) => {
    setModalData({ title, content, contentCategory })
    currentEndpointRef.current = endpoint
    setModalOpen(true)
  }, [])

  const closeModal = useCallback(() => {
    setModalOpen(false)
    currentEndpointRef.current = null
  }, [])

  const saveConfig = useCallback(async (content) => {
    const currentEndpoint = currentEndpointRef.current
    if (!currentEndpoint) return

    try {
      let payload
      let method = 'POST'
      if (currentEndpoint === 'banners') {
        const messages = content.split('\n').map(line => line.trim()).filter(line => line)
        payload = { messages }
      } else if (currentEndpoint === 'help-config') {
        // help-config uses PUT to replace the full markdown document
        payload = { content }
        method = 'PUT'
      } else {
        const fileType = currentEndpoint.includes('json') ? 'json' :
                       currentEndpoint.includes('yml') || currentEndpoint.includes('yaml') ? 'yaml' : 'text'
        payload = { content, file_type: fileType }
      }

      const response = await fetch(`/admin/${currentEndpoint}`, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })

      if (!response.ok) {
        const error = await response.json()
        throw new Error(error.detail || `HTTP ${response.status}`)
      }

      const result = await response.json()
      addNotification('Configuration saved successfully: ' + result.message, 'success')

      await loadSystemStatus()
    } catch (err) {
      addNotification('Error saving configuration: ' + err.message, 'error')
    }
  }, [addNotification, loadSystemStatus])

  const downloadLogs = useCallback(() => {
    try {
      const link = document.createElement('a')
      link.href = '/admin/logs/download'
      link.download = `app_log_${new Date().toISOString().slice(0, 19).replace(/:/g, '-')}.log`
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)

      addNotification('Log download started', 'success')
    } catch (err) {
      addNotification('Error downloading logs: ' + err.message, 'error')
    }
  }, [addNotification])

  return {
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
  }
}

export default useAdminConfigActions
