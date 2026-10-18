import { useState, useEffect, useRef } from 'react'

const DEFAULT_SETTINGS = {
  llmTemperature: 0.7,
  maxIterations: 10,
  autoApproveTools: false,  // User-level auto-approval for non-admin-required tools
  debugMode: false,  // Show raw tool call input/output in chat (#135)
  compactMessages: true,  // Compact tool/approval/system rows vs. classic avatar+header bubbles (#673)
  reduceHelpText: false  // Hide optional onboarding copy, explanatory links, and hover hints
}

export function useSettings() {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)
  const [isLoaded, setIsLoaded] = useState(false)
  // Values changed through this hook, kept separately from `settings` so a
  // failed storage write can still win over a stale stored snapshot on the
  // next update.
  const inMemoryRef = useRef({})

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

  // Function to update settings. Merge onto the latest persisted snapshot,
  // not just this hook's in-memory copy: the Settings panel keeps its own
  // draft and may have saved other fields in the same session, and a
  // context-only update must not write those older values back. Values
  // updated through this hook are applied last so a failed write cannot be
  // undone by the next call reading the older stored snapshot.
  const updateSettings = (newSettings) => {
    let latest = { ...DEFAULT_SETTINGS }
    try {
      const saved = localStorage.getItem('chatui-settings')
      if (saved) latest = { ...DEFAULT_SETTINGS, ...JSON.parse(saved) }
    } catch (error) {
      console.error('Failed to read settings from localStorage:', error)
    }
    // Keep only keys this hook changed: untouched keys must keep following
    // later writes from the Settings panel or another tab.
    inMemoryRef.current = { ...inMemoryRef.current, ...newSettings }
    const updatedSettings = { ...latest, ...inMemoryRef.current }
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
    inMemoryRef.current = { ...DEFAULT_SETTINGS }
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