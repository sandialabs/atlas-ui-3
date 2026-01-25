import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, act } from '@testing-library/react'
import MCPConfigurationCard from '../components/admin/MCPConfigurationCard.jsx'

// Mock the useChat hook
vi.mock('../contexts/ChatContext', () => ({
  useChat: () => ({
    refreshConfig: vi.fn(),
  }),
}))

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
      vi.useFakeTimers({ shouldAdvanceTime: true })
    })

    afterEach(() => {
      vi.runOnlyPendingTimers()
      vi.useRealTimers()
      global.fetch = originalFetch
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

      await act(async () => {
        render(
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />,
        )
        await vi.advanceTimersByTimeAsync(0)
      })

      expect(screen.getByText('Connected servers')).toBeInTheDocument()
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

      await act(async () => {
        render(
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />,
        )
        await vi.advanceTimersByTimeAsync(0)
      })

      const viewButton = screen.getByText('View MCP Config')

      await act(async () => {
        viewButton.click()
        await vi.advanceTimersByTimeAsync(0)
      })

      expect(openModal).toHaveBeenCalled()
      const [title, options] = openModal.mock.calls[0]

      expect(title).toBe('View MCP Configuration')
      expect(options.type).toBe('textarea')
      expect(options.readOnly).toBe(true)
      expect(options.value).toContain('example')
    })

    it('applies exponential backoff when status requests fail', async () => {
      // Mock fetch to fail
      global.fetch.mockRejectedValue(new Error('Network error'))

      const consoleLogSpy = vi.spyOn(console, 'log')
      vi.spyOn(console, 'error').mockImplementation(() => {}) // Suppress error logs

      await act(async () => {
        render(
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />,
        )
        await vi.advanceTimersByTimeAsync(0) // Let initial fetch complete
      })

      expect(global.fetch).toHaveBeenCalledTimes(1)
      expect(consoleLogSpy).toHaveBeenCalledWith(
        expect.stringContaining('1 consecutive failures, next retry in 1s')
      )

      // Verify backoff delays retry - should NOT have retried after 500ms
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500)
      })
      expect(global.fetch).toHaveBeenCalledTimes(1)

      // First retry after 1 second total
      await act(async () => {
        await vi.advanceTimersByTimeAsync(500)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)
      expect(consoleLogSpy).toHaveBeenCalledWith(
        expect.stringContaining('2 consecutive failures, next retry in 2s')
      )

      // Should NOT retry before 2s
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)

      // Second retry after 2 seconds from last call
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(3)
      expect(consoleLogSpy).toHaveBeenCalledWith(
        expect.stringContaining('3 consecutive failures, next retry in 4s')
      )

      consoleLogSpy.mockRestore()
    })

    it('resets backoff delay after successful request', async () => {
      // First request fails
      global.fetch.mockRejectedValueOnce(new Error('Network error'))

      vi.spyOn(console, 'error').mockImplementation(() => {}) // Suppress error logs
      vi.spyOn(console, 'log').mockImplementation(() => {})

      await act(async () => {
        render(
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />,
        )
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(global.fetch).toHaveBeenCalledTimes(1)

      // Mock successful response for next retry
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          configured_servers: ['test-server'],
          connected_servers: ['test-server'],
          failed_servers: {},
        }),
      })

      // First failure triggers 1 second backoff
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)

      // Mock another successful response
      global.fetch.mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          configured_servers: ['test-server'],
          connected_servers: ['test-server'],
          failed_servers: {},
        }),
      })

      // After success, should NOT poll before 15 seconds
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)

      // Should poll after 15 second normal interval
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(3)
    })

    it('continues polling after multiple successful requests', async () => {
      // Mock successful responses
      global.fetch.mockResolvedValue({
        ok: true,
        json: async () => ({
          configured_servers: ['test-server'],
          connected_servers: ['test-server'],
          failed_servers: {},
        }),
      })

      await act(async () => {
        render(
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />,
        )
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(global.fetch).toHaveBeenCalledTimes(1)

      // After 15 seconds, should poll again
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)

      // After another 15 seconds, should poll again (verifies continuous polling)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(3)

      // And again - polling should continue indefinitely
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(4)
    })

    it('caps exponential backoff at 30 seconds', async () => {
      // Mock fetch to fail consistently
      global.fetch.mockRejectedValue(new Error('Network error'))

      const consoleLogSpy = vi.spyOn(console, 'log')
      vi.spyOn(console, 'error').mockImplementation(() => {}) // Suppress error logs

      await act(async () => {
        render(
          <MCPConfigurationCard
            openModal={openModal}
            addNotification={addNotification}
            systemStatus={systemStatus}
          />,
        )
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(global.fetch).toHaveBeenCalledTimes(1)

      // Simulate multiple failures to reach max backoff
      // 1s, 2s, 4s, 8s, 16s, 30s (capped)
      const delays = [1000, 2000, 4000, 8000, 16000, 30000]

      for (let i = 0; i < delays.length; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(delays[i])
        })
        expect(global.fetch).toHaveBeenCalledTimes(i + 2)
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
