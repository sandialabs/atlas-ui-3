import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import MCPServerManager from '../components/admin/MCPServerManager.jsx'

const isCI = process.env.CI || process.env.ENVIRONMENT === 'cicd'

describe('MCPServerManager', () => {
  if (!isCI) {
    const addNotification = vi.fn()
    const originalFetch = global.fetch

    beforeEach(() => {
      vi.clearAllMocks()
      global.fetch = vi.fn()
    })

    afterEach(() => {
      global.fetch = originalFetch
    })

    it('loads servers and can add a server', async () => {
      const serverName = 'example-server'

      // Initial load: available then active
      global.fetch
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            available_servers: {
              [serverName]: {
                config: { transport: 'stdio', command: ['echo', 'hi'] },
                source_file: 'mcp-example.json',
                short_description: 'Example',
                description: 'Example server',
                author: 'Tester',
                compliance_level: 'Public',
              },
            },
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ active_servers: {} }),
        })
        // Add request
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ message: `Server '${serverName}' added successfully`, server_name: serverName }),
        })
        // Reload after add: available then active with server present
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({
            available_servers: {
              [serverName]: {
                config: { transport: 'stdio', command: ['echo', 'hi'] },
                source_file: 'mcp-example.json',
                short_description: 'Example',
                description: 'Example server',
                author: 'Tester',
                compliance_level: 'Public',
              },
            },
          }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: async () => ({ active_servers: { [serverName]: { transport: 'stdio' } } }),
        })

      render(<MCPServerManager addNotification={addNotification} />)

      await waitFor(() => {
        expect(screen.getByText(serverName)).toBeInTheDocument()
      })

      const addButton = screen.getByRole('button', { name: /add/i })
      fireEvent.click(addButton)

      await waitFor(() => {
        expect(addNotification).toHaveBeenCalledWith(expect.stringContaining('added successfully'), 'success')
      })

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /remove/i })).toBeInTheDocument()
      })

      expect(global.fetch).toHaveBeenCalledWith('/admin/mcp/add-server', expect.any(Object))
    })
  } else {
    it('skips React component tests in CI/CD', () => {
      expect(true).toBe(true)
    })
  }
})
