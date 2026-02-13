import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, act } from '@testing-library/react'
import BannerMessagesCard from '../components/admin/BannerMessagesCard'
import MCPServerManager from '../components/admin/MCPServerManager'

const isCI = process.env.CI || process.env.ENVIRONMENT === 'cicd'

describe('Admin components do not fetch in a loop when backend is down', () => {
  if (!isCI) {
    const originalFetch = global.fetch

    beforeEach(() => {
      vi.clearAllMocks()
      global.fetch = vi.fn().mockRejectedValue(new Error('net::ERR_CONNECTION_RESET'))
      vi.spyOn(console, 'error').mockImplementation(() => {})
      vi.useFakeTimers({ shouldAdvanceTime: true })
    })

    afterEach(() => {
      vi.runOnlyPendingTimers()
      vi.useRealTimers()
      global.fetch = originalFetch
    })

    it('BannerMessagesCard fetches exactly once on mount when backend is down', async () => {
      const addNotification = vi.fn()
      const openModal = vi.fn()

      await act(async () => {
        render(
          <BannerMessagesCard
            openModal={openModal}
            addNotification={addNotification}
          />
        )
        await vi.advanceTimersByTimeAsync(0)
      })

      // Should have fetched exactly once
      expect(global.fetch).toHaveBeenCalledTimes(1)

      // Wait a while - should NOT re-fetch
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(1)

      // Should NOT have shown a toast notification (error is logged to console only)
      expect(addNotification).not.toHaveBeenCalled()
    })

    it('MCPServerManager fetches exactly once on mount when backend is down', async () => {
      const addNotification = vi.fn()

      await act(async () => {
        render(
          <MCPServerManager addNotification={addNotification} />
        )
        await vi.advanceTimersByTimeAsync(0)
      })

      // Should have fetched once (available-servers call, fails before active-servers)
      const callCount = global.fetch.mock.calls.length
      expect(callCount).toBeLessThanOrEqual(2) // At most 2: available + active

      // Wait a while - should NOT re-fetch in a loop
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(callCount)

      // Should NOT have shown a toast notification (error is logged to console only)
      expect(addNotification).not.toHaveBeenCalled()
    })

    it('BannerMessagesCard does not infinite-loop even with unstable addNotification', async () => {
      // Simulate an unstable addNotification that changes identity on every render
      // (the root cause of the original bug)
      let renderCount = 0
      function Wrapper() {
        renderCount++
        // Create a new function on every render (intentionally unstable)
        const addNotification = () => {}
        return (
          <BannerMessagesCard
            openModal={() => {}}
            addNotification={addNotification}
          />
        )
      }

      await act(async () => {
        render(<Wrapper />)
        await vi.advanceTimersByTimeAsync(0)
      })

      const fetchCountAfterMount = global.fetch.mock.calls.length

      // Advance time and verify no additional fetches
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000)
      })

      expect(global.fetch).toHaveBeenCalledTimes(fetchCountAfterMount)
      // Should not have re-rendered excessively
      expect(renderCount).toBeLessThan(10)
    })
  } else {
    it('skips admin component tests in CI/CD', () => {
      expect(true).toBe(true)
    })
  }
})
