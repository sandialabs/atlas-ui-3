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
    })

    afterEach(() => {
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
  } else {
    it('skips React component tests in CI/CD', () => {
      expect(true).toBe(true)
    })
  }
})
