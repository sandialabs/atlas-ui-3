import { useState, useEffect } from 'react'

const DEFAULT_SETTINGS = {
  llmTemperature: 0.7,
  maxIterations: 10,
  agentLoopStrategy: 'agentic',
  autoApproveTools: false  // User-level auto-approval for non-admin-required tools
}

export function useSettings() {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)
  const [isLoaded, setIsLoaded] = useState(false)

  // Load settings from localStorage on mount
  useEffect(() => {
    try {
      const savedSettings = localStorage.getItem('chatui-settings')
      if (savedSettings) {
        try {
          const parsed = JSON.parse(savedSettings)
          setSettings({ ...DEFAULT_SETTINGS, ...parsed })
        } catch (error) {
          console.error('Failed to parse saved settings:', error)
          setSettings(DEFAULT_SETTINGS)
        }
      }
    } catch (error) {
      console.error('Failed to access localStorage:', error)
      setSettings(DEFAULT_SETTINGS)
    }
    setIsLoaded(true)
  }, [])

  // Function to update settings
  const updateSettings = (newSettings) => {
    const updatedSettings = { ...settings, ...newSettings }
    setSettings(updatedSettings)
    try {
      localStorage.setItem('chatui-settings', JSON.stringify(updatedSettings))
    } catch (error) {
      console.error('Failed to save settings to localStorage:', error)
    }
    return updatedSettings
  }

  // Function to reset settings to defaults
  const resetSettings = () => {
    setSettings(DEFAULT_SETTINGS)
    try {
      localStorage.setItem('chatui-settings', JSON.stringify(DEFAULT_SETTINGS))
    } catch (error) {
      console.error('Failed to save default settings to localStorage:', error)
    }
    return DEFAULT_SETTINGS
  }

  // Function to get a specific setting
  const getSetting = (key) => {
    return settings[key] ?? DEFAULT_SETTINGS[key]
  }

  return {
    settings,
    isLoaded,
    updateSettings,
    resetSettings,
    getSetting
  }
}