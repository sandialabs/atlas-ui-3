/**
 * Tests for useSettings hook
 */

import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useSettings } from '../hooks/useSettings.js'

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
}

Object.defineProperty(window, 'localStorage', {
  value: localStorageMock,
  writable: true,
})

// Mock console.error to avoid noise in tests
const consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

describe('useSettings', () => {
  beforeEach(() => {
    // Clear all mocks before each test
    vi.clearAllMocks()
    localStorageMock.getItem.mockReturnValue(null)
  })

  afterEach(() => {
    consoleErrorSpy.mockClear()
  })

  describe('initialization', () => {
    it('should initialize with default settings when localStorage is empty', () => {
      const { result } = renderHook(() => useSettings())

      expect(result.current.settings).toEqual({
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })
      expect(result.current.isLoaded).toBe(true)
      expect(localStorageMock.getItem).toHaveBeenCalledWith('chatui-settings')
    })

    it('should load settings from localStorage when available', () => {
      const savedSettings = {
        llmTemperature: 0.5,
        maxIterations: 15,
        agentLoopStrategy: 'act-only',
        autoApproveTools: true
      }
      localStorageMock.getItem.mockReturnValue(JSON.stringify(savedSettings))

      const { result } = renderHook(() => useSettings())

      expect(result.current.settings).toEqual(savedSettings)
      expect(result.current.isLoaded).toBe(true)
    })

    it('should merge saved settings with defaults', () => {
      const partialSettings = {
        llmTemperature: 0.9,
        maxIterations: 20
      }
      localStorageMock.getItem.mockReturnValue(JSON.stringify(partialSettings))

      const { result } = renderHook(() => useSettings())

      expect(result.current.settings).toEqual({
        llmTemperature: 0.9,
        maxIterations: 20,
        agentLoopStrategy: 'agentic', // default value
        autoApproveTools: false // default value
      })
    })

    it('should handle invalid JSON in localStorage gracefully', () => {
      localStorageMock.getItem.mockReturnValue('invalid json')

      const { result } = renderHook(() => useSettings())

      expect(result.current.settings).toEqual({
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })
      expect(consoleErrorSpy).toHaveBeenCalledWith('Failed to parse saved settings:', expect.any(Error))
    })

    it('should start with isLoaded as false and set to true after initialization', () => {
      const { result } = renderHook(() => useSettings())

      // After the effect runs, isLoaded should be true
      expect(result.current.isLoaded).toBe(true)
    })
  })

  describe('updateSettings', () => {
    it('should update settings and save to localStorage', () => {
      const { result } = renderHook(() => useSettings())

      act(() => {
        const updatedSettings = result.current.updateSettings({
          llmTemperature: 0.8,
          maxIterations: 12
        })

        expect(updatedSettings).toEqual({
          llmTemperature: 0.8,
          maxIterations: 12,
          agentLoopStrategy: 'agentic',
          autoApproveTools: false
        })
      })

      expect(result.current.settings).toEqual({
        llmTemperature: 0.8,
        maxIterations: 12,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })

      expect(localStorageMock.setItem).toHaveBeenCalledWith(
        'chatui-settings',
        JSON.stringify({
          llmTemperature: 0.8,
          maxIterations: 12,
          agentLoopStrategy: 'agentic',
          autoApproveTools: false
        })
      )
    })

    it('should merge new settings with existing settings', () => {
      const { result } = renderHook(() => useSettings())

      // First update
      act(() => {
        result.current.updateSettings({ llmTemperature: 0.8 })
      })

      // Second update
      act(() => {
        result.current.updateSettings({ maxIterations: 15 })
      })

      expect(result.current.settings).toEqual({
        llmTemperature: 0.8,
        maxIterations: 15,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })
    })

    it('should handle updating with empty object', () => {
      const { result } = renderHook(() => useSettings())

      act(() => {
        result.current.updateSettings({})
      })

      expect(result.current.settings).toEqual({
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })
    })

    it('should handle updating with null values', () => {
      const { result } = renderHook(() => useSettings())

      act(() => {
        result.current.updateSettings({ llmTemperature: null })
      })

      expect(result.current.settings.llmTemperature).toBeNull()
    })
  })

  describe('resetSettings', () => {
    it('should reset settings to defaults and save to localStorage', () => {
      const { result } = renderHook(() => useSettings())

      // First, update settings
      act(() => {
        result.current.updateSettings({
          llmTemperature: 0.9,
          maxIterations: 20,
          agentLoopStrategy: 'act-only',
          autoApproveTools: true
        })
      })

      // Then reset
      act(() => {
        const resetSettings = result.current.resetSettings()

        expect(resetSettings).toEqual({
          llmTemperature: 0.7,
          maxIterations: 10,
          agentLoopStrategy: 'agentic',
          autoApproveTools: false
        })
      })

      expect(result.current.settings).toEqual({
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })

      expect(localStorageMock.setItem).toHaveBeenLastCalledWith(
        'chatui-settings',
        JSON.stringify({
          llmTemperature: 0.7,
          maxIterations: 10,
          agentLoopStrategy: 'agentic',
          autoApproveTools: false
        })
      )
    })
  })

  describe('getSetting', () => {
    it('should return the current value for existing settings', () => {
      const { result } = renderHook(() => useSettings())

      expect(result.current.getSetting('llmTemperature')).toBe(0.7)
      expect(result.current.getSetting('maxIterations')).toBe(10)
      expect(result.current.getSetting('agentLoopStrategy')).toBe('agentic')
      expect(result.current.getSetting('autoApproveTools')).toBe(false)
    })

    it('should return default value for non-existent settings', () => {
      const { result } = renderHook(() => useSettings())

      expect(result.current.getSetting('nonExistentSetting')).toBeUndefined()
    })

    it('should return default value when current setting is undefined', () => {
      const { result } = renderHook(() => useSettings())

      // Update settings to have undefined value
      act(() => {
        result.current.updateSettings({ llmTemperature: undefined })
      })

      expect(result.current.getSetting('llmTemperature')).toBe(0.7) // default value
    })

    it('should return current value even if it differs from default', () => {
      const { result } = renderHook(() => useSettings())

      act(() => {
        result.current.updateSettings({ llmTemperature: 0.9 })
      })

      expect(result.current.getSetting('llmTemperature')).toBe(0.9)
    })
  })

  describe('edge cases', () => {
    it('should handle localStorage throwing errors', () => {
      localStorageMock.getItem.mockImplementation(() => {
        throw new Error('localStorage not available')
      })

      const { result } = renderHook(() => useSettings())

      expect(result.current.settings).toEqual({
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })
    })

    it('should handle setItem throwing errors during update', () => {
      localStorageMock.setItem.mockImplementation(() => {
        throw new Error('localStorage quota exceeded')
      })

      const { result } = renderHook(() => useSettings())

      // Should still update the state even if localStorage fails
      act(() => {
        result.current.updateSettings({ llmTemperature: 0.8 })
      })

      expect(result.current.settings.llmTemperature).toBe(0.8)
    })

    it('should handle complex nested objects in settings', () => {
      const complexSettings = {
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false,
        customConfig: {
          nested: {
            value: 'test'
          }
        }
      }

      const { result } = renderHook(() => useSettings())

      act(() => {
        result.current.updateSettings(complexSettings)
      })

      expect(result.current.settings).toEqual(complexSettings)
    })
  })

  describe('integration scenarios', () => {
    it('should work correctly with multiple updates and resets', () => {
      const { result } = renderHook(() => useSettings())

      // Multiple updates
      act(() => {
        result.current.updateSettings({ llmTemperature: 0.8 })
      })
      
      act(() => {
        result.current.updateSettings({ maxIterations: 15 })
      })
      
      act(() => {
        result.current.updateSettings({ autoApproveTools: true })
      })

      expect(result.current.settings).toEqual({
        llmTemperature: 0.8,
        maxIterations: 15,
        agentLoopStrategy: 'agentic',
        autoApproveTools: true
      })

      // Reset
      act(() => {
        result.current.resetSettings()
      })

      expect(result.current.settings).toEqual({
        llmTemperature: 0.7,
        maxIterations: 10,
        agentLoopStrategy: 'agentic',
        autoApproveTools: false
      })
    })

    it('should maintain consistency between getSetting and direct access', () => {
      const { result } = renderHook(() => useSettings())

      act(() => {
        result.current.updateSettings({ llmTemperature: 0.9 })
      })

      expect(result.current.getSetting('llmTemperature')).toBe(result.current.settings.llmTemperature)
      expect(result.current.getSetting('maxIterations')).toBe(result.current.settings.maxIterations)
    })
  })
})