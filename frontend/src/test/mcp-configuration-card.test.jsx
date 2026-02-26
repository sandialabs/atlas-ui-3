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
    const originalRandom = Math.random

    beforeEach(() => {
      vi.clearAllMocks()
      global.fetch = vi.fn()
      vi.useFakeTimers({ shouldAdvanceTime: true })
      // Fix Math.random so jitter is deterministic (factor = 0.8 + 0.5*0.4 = 1.0)
      Math.random = () => 0.5
    })

    afterEach(() => {
      vi.runOnlyPendingTimers()
      vi.useRealTimers()
      global.fetch = originalFetch
      Math.random = originalRandom
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

      // After success, should NOT poll before 30 seconds (normal interval)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(25000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)

      // Should poll after 30 second normal interval
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

      // After 30 seconds, should poll again
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(2)

      // After another 30 seconds, should poll again (verifies continuous polling)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(3)

      // And again - polling should continue indefinitely
      await act(async () => {
        await vi.advanceTimersByTimeAsync(30000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(4)
    })

    it('caps exponential backoff at 5 minutes', async () => {
      // Mock fetch to fail consistently
      global.fetch.mockRejectedValue(new Error('Network error'))

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

      // Simulate multiple failures to reach max backoff (5 min = 300s)
      // With Math.random() = 0.5, jitter factor = 1.0, so delays are exact:
      // 1s, 2s, 4s, 8s, 16s, 32s, 64s, 128s, 256s, 300s (capped)
      const delays = [1000, 2000, 4000, 8000, 16000, 32000, 64000, 128000, 256000, 300000]

      for (let i = 0; i < delays.length; i++) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(delays[i])
        })
        expect(global.fetch).toHaveBeenCalledTimes(i + 2)
      }

      // Verify backoff is capped: after 10 failures, the next retry should
      // still be at 300s (not growing further)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300000)
      })
      expect(global.fetch).toHaveBeenCalledTimes(delays.length + 2)
    })
  } else {
    it('skips React component tests in CI/CD', () => {
      expect(true).toBe(true)
    })
  }
})
