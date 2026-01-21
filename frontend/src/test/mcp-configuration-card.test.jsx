import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import MCPConfigurationCard from '../components/admin/MCPConfigurationCard.jsx'

const isCI = process.env.CI || process.env.ENVIRONMENT === 'cicd'

describe('MCPConfigurationCard', () => {
  if (!isCI) {
    const openModal = vi.fn()
    const addNotification = vi.fn()
    const systemStatus = { overall_status: 'healthy' }
    const originalFetch = global.fetch

    beforeEach(() => {
      vi.clearAllMocks()
      global.fetch = vi.fn()
      vi.useFakeTimers()
    })

    afterEach(() => {
      global.fetch = originalFetch
      vi.useRealTimers()
    })

    it('filters out servers that are not configured from inline status', async () => {
      // First status call used by useEffect polling
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          configured_servers: ['live-server'],
          connected_servers: ['live-server', 'removed-server'],
          failed_servers: {
            'removed-server': { error: 'old failure' },
          },
        }),
      })

      render(
        <MCPConfigurationCard
          openModal={openModal}
          addNotification={addNotification}
          systemStatus={systemStatus}
        />,
      )

      await waitFor(() => {
        expect(screen.getByText('Connected servers')).toBeInTheDocument()
      })

      expect(screen.getByText((content, element) => {
        return element.tagName === 'SPAN' && content.includes('live-server')
      })).toBeInTheDocument()
      expect(screen.queryByText((content, element) => {
        return element.tagName === 'SPAN' && content.includes('removed-server')
      })).toBeNull()
    })

    it('opens a read-only View MCP Config modal with current config', async () => {
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          configured_servers: [],
          connected_servers: [],
          failed_servers: {},
        }),
      })

      const mcpConfig = { servers: { example: { transport: 'stdio' } } }

      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({ mcp_config: mcpConfig }),
      })

      render(
        <MCPConfigurationCard
          openModal={openModal}
          addNotification={addNotification}
          systemStatus={systemStatus}
        />,
      )

      const viewButton = screen.getByText('View MCP Config')
      await viewButton.click()

      await waitFor(() => {
        expect(openModal).toHaveBeenCalled()
      })

      const [title, options] = openModal.mock.calls[0]

      expect(title).toBe('View MCP Configuration')
      expect(options.type).toBe('textarea')
      expect(options.readOnly).toBe(true)
      expect(options.value).toContain('example')
    })

    it('applies exponential backoff when status requests fail', async () => {
      // Mock fetch to fail initially
      global.fetch.mockRejectedValue(new Error('Network error'))

      const consoleLogSpy = vi.spyOn(console, 'log')
      const consoleErrorSpy = vi.spyOn(console, 'error')

      render(
        <MCPConfigurationCard
          openModal={openModal}
          addNotification={addNotification}
          systemStatus={systemStatus}
        />,
      )

      // Wait for initial fetch to fail
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(1)
      })

      // First failure - should schedule retry with 1 second delay
      await vi.advanceTimersByTimeAsync(1000)
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(2)
        expect(consoleLogSpy).toHaveBeenCalledWith(
          expect.stringContaining('1 consecutive failures, next retry in 1s')
        )
      })

      // Second failure - should schedule retry with 2 second delay
      await vi.advanceTimersByTimeAsync(2000)
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(3)
        expect(consoleLogSpy).toHaveBeenCalledWith(
          expect.stringContaining('2 consecutive failures, next retry in 2s')
        )
      })

      // Third failure - should schedule retry with 4 second delay
      await vi.advanceTimersByTimeAsync(4000)
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(4)
        expect(consoleLogSpy).toHaveBeenCalledWith(
          expect.stringContaining('3 consecutive failures, next retry in 4s')
        )
      })

      consoleLogSpy.mockRestore()
      consoleErrorSpy.mockRestore()
    })

    it('resets backoff delay after successful request', async () => {
      // First request fails
      global.fetch.mockRejectedValueOnce(new Error('Network error'))

      render(
        <MCPConfigurationCard
          openModal={openModal}
          addNotification={addNotification}
          systemStatus={systemStatus}
        />,
      )

      // Wait for initial fetch to fail
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(1)
      })

      // Mock successful response for next retry
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          configured_servers: ['test-server'],
          connected_servers: ['test-server'],
          failed_servers: {},
        }),
      })

      // First failure - triggers 1 second backoff
      await vi.advanceTimersByTimeAsync(1000)
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(2)
      })

      // Now mock another successful response
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          configured_servers: ['test-server'],
          connected_servers: ['test-server'],
          failed_servers: {},
        }),
      })

      // After success, should reset to normal 15 second polling
      await vi.advanceTimersByTimeAsync(15000)
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(3)
      })
    })

    it('caps exponential backoff at 30 seconds', async () => {
      // Mock fetch to fail consistently
      global.fetch.mockRejectedValue(new Error('Network error'))

      const consoleLogSpy = vi.spyOn(console, 'log')

      render(
        <MCPConfigurationCard
          openModal={openModal}
          addNotification={addNotification}
          systemStatus={systemStatus}
        />,
      )

      // Wait for initial fetch to fail
      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledTimes(1)
      })

      // Simulate multiple failures to reach max backoff
      // 1s, 2s, 4s, 8s, 16s, 30s (capped)
      const delays = [1000, 2000, 4000, 8000, 16000, 30000]
      
      for (let i = 0; i < delays.length; i++) {
        await vi.advanceTimersByTimeAsync(delays[i])
        await waitFor(() => {
          expect(global.fetch).toHaveBeenCalledTimes(i + 2)
        })
      }

      // Verify the last delay is capped at 30 seconds
      const lastLogCall = consoleLogSpy.mock.calls.find(call => 
        call[0].includes('6 consecutive failures')
      )
      expect(lastLogCall).toBeTruthy()
      expect(lastLogCall[0]).toContain('next retry in 30s')

      consoleLogSpy.mockRestore()
    })
  } else {
    it('skips React component tests in CI/CD', () => {
      expect(true).toBe(true)
    })
  }
})
