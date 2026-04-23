import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import React from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import TelemetryDashboard from '../components/TelemetryDashboard'

const overviewPayload = {
  range: '24h',
  turns: 42,
  tool_calls: 80,
  tool_success_rate: 0.95,
  llm_calls: 100,
  llm_retries_total: 3,
  rag_queries: 12,
  llm_latency_p50_ms: 320,
  llm_latency_p95_ms: 1800,
}

const toolsPayload = {
  range: '24h',
  tools: [
    {
      tool_name: 'alpha_tool',
      call_count: 10,
      success_rate: 0.8,
      failure_count: 2,
      duration_p50_ms: 100,
      duration_p95_ms: 400,
      last_failure_start_ns: 1_700_000_000_000_000_000,
      last_failure_error_type: 'TimeoutError',
    },
  ],
}

const toolFailuresPayload = {
  tool_name: 'alpha_tool',
  failures: [
    {
      span_id: 'span-1',
      start_time_ns: 1_700_000_000_000_000_000,
      duration_ms: 123,
      error_type: 'TimeoutError',
      error_message: 'upstream timed out',
    },
  ],
}

const llmPayload = {
  range: '24h',
  models: [
    {
      model: 'gpt-test',
      call_count: 50,
      p50_ms: 200,
      p95_ms: 900,
      p99_ms: 1500,
      input_tokens_total: 10000,
      output_tokens_total: 2500,
      retry_rate: 0.04,
      error_count: 1,
    },
  ],
}

const ragPayload = {
  range: '24h',
  sources: [
    {
      data_source: 'docs',
      query_count: 20,
      docs_retrieved_total: 100,
      docs_used_total: 40,
      retrieval_to_use_ratio: 0.4,
      top_score_p50: 0.72,
      top_score_p95: 0.94,
      top_score_max: 0.99,
    },
  ],
}

function mockApi(handler) {
  global.fetch = vi.fn(async (url) => {
    const out = handler(url)
    if (out instanceof Error) {
      return { ok: false, status: 500, json: async () => ({}) }
    }
    if (out && out.__status) {
      return { ok: false, status: out.__status, json: async () => ({}) }
    }
    return { ok: true, status: 200, json: async () => out }
  })
}

function renderDashboard() {
  return render(
    <MemoryRouter>
      <TelemetryDashboard />
    </MemoryRouter>
  )
}

describe('TelemetryDashboard', () => {
  const originalFetch = global.fetch

  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it('renders admin-access-required view when /status returns 403', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) return { __status: 403 }
      return {}
    })

    await act(async () => {
      renderDashboard()
    })

    await waitFor(() => {
      expect(screen.getByText(/Admin access required/i)).toBeInTheDocument()
    })
  })

  it('fetches status + overview on mount and renders overview stats', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) {
        return { backend: 'file', path: '/tmp/spans.jsonl', size_bytes: 12345 }
      }
      if (url.includes('/admin/telemetry/overview')) return overviewPayload
      return {}
    })

    await act(async () => {
      renderDashboard()
    })

    await waitFor(() => {
      expect(screen.getByText('Chat turns')).toBeInTheDocument()
      expect(screen.getByText('42')).toBeInTheDocument()
      expect(screen.getByText(/success rate 95.0%/)).toBeInTheDocument()
    })

    const calls = global.fetch.mock.calls.map(c => c[0])
    expect(calls.some(u => u.includes('/admin/telemetry/status'))).toBe(true)
    expect(calls.some(u => u.includes('/admin/telemetry/overview?range=24h'))).toBe(true)
  })

  it('switches to Tool health tab and loads per-tool rollup', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) return { backend: 'file' }
      if (url.includes('/admin/telemetry/overview')) return overviewPayload
      if (url.includes('/admin/telemetry/tools') && !url.includes('/failures')) return toolsPayload
      return {}
    })

    await act(async () => {
      renderDashboard()
    })

    await waitFor(() => {
      expect(screen.getByText('Chat turns')).toBeInTheDocument()
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Tool health/i }))
    })

    await waitFor(() => {
      expect(screen.getByText('alpha_tool')).toBeInTheDocument()
    })

    const calls = global.fetch.mock.calls.map(c => c[0])
    expect(calls.some(u => u.includes('/admin/telemetry/tools?range=24h'))).toBe(true)
  })

  it('loads per-tool failures when a tool row is clicked and drops stale responses', async () => {
    const deferred = []
    global.fetch = vi.fn(async (url) => {
      if (url.includes('/admin/telemetry/status')) {
        return { ok: true, status: 200, json: async () => ({ backend: 'file' }) }
      }
      if (url.includes('/admin/telemetry/overview')) {
        return { ok: true, status: 200, json: async () => overviewPayload }
      }
      if (url.includes('/admin/telemetry/tools/alpha_tool/failures')) {
        return new Promise((resolve) => {
          deferred.push(() =>
            resolve({ ok: true, status: 200, json: async () => toolFailuresPayload })
          )
        })
      }
      if (url.includes('/admin/telemetry/tools')) {
        return { ok: true, status: 200, json: async () => toolsPayload }
      }
      return { ok: true, status: 200, json: async () => ({}) }
    })

    await act(async () => {
      renderDashboard()
    })

    await waitFor(() => expect(screen.getByText('Chat turns')).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Tool health/i }))
    })
    await waitFor(() => expect(screen.getByText('alpha_tool')).toBeInTheDocument())

    // Click to open failures (first request, held open)
    await act(async () => {
      fireEvent.click(screen.getByText('alpha_tool'))
    })
    expect(await screen.findByText(/Loading failures/i)).toBeInTheDocument()

    // Close → reopen (second request, superseding the first)
    await act(async () => {
      fireEvent.click(screen.getByText('alpha_tool'))
    })
    await act(async () => {
      fireEvent.click(screen.getByText('alpha_tool'))
    })

    // Resolve the stale first request BEFORE the newer one — its response must
    // be discarded by the request-id ref guard in ToolsView.
    await act(async () => {
      if (deferred.length > 0) deferred[0]()
    })

    // Stale response should not populate failures list
    expect(screen.queryByText('upstream timed out')).not.toBeInTheDocument()

    // Resolve the newest request — this one should land.
    await act(async () => {
      if (deferred.length > 1) deferred[deferred.length - 1]()
    })

    await waitFor(() => {
      expect(screen.getByText('upstream timed out')).toBeInTheDocument()
    })
  })

  it('LLM tab fetches /admin/telemetry/llm and renders model rollup', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) return { backend: 'file' }
      if (url.includes('/admin/telemetry/overview')) return overviewPayload
      if (url.includes('/admin/telemetry/llm')) return llmPayload
      return {}
    })

    await act(async () => {
      renderDashboard()
    })
    await waitFor(() => expect(screen.getByText('Chat turns')).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /LLM performance/i }))
    })

    await waitFor(() => expect(screen.getByText('gpt-test')).toBeInTheDocument())
  })

  it('RAG tab fetches /admin/telemetry/rag and renders per-source rollup', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) return { backend: 'file' }
      if (url.includes('/admin/telemetry/overview')) return overviewPayload
      if (url.includes('/admin/telemetry/rag')) return ragPayload
      return {}
    })

    await act(async () => {
      renderDashboard()
    })
    await waitFor(() => expect(screen.getByText('Chat turns')).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /RAG effectiveness/i }))
    })

    await waitFor(() => expect(screen.getByText('docs')).toBeInTheDocument())
  })

  it('refetches overview when range selector changes', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) return { backend: 'file' }
      if (url.includes('/admin/telemetry/overview')) return overviewPayload
      return {}
    })

    await act(async () => {
      renderDashboard()
    })
    await waitFor(() => expect(screen.getByText('Chat turns')).toBeInTheDocument())

    const select = screen.getByDisplayValue('Last 24 hours')
    await act(async () => {
      fireEvent.change(select, { target: { value: '7d' } })
    })

    await waitFor(() => {
      const calls = global.fetch.mock.calls.map(c => c[0])
      expect(calls.some(u => u.includes('/admin/telemetry/overview?range=7d'))).toBe(true)
    })
  })

  it('Session drill-down requires a non-empty identifier', async () => {
    mockApi((url) => {
      if (url.includes('/admin/telemetry/status')) return { backend: 'file' }
      if (url.includes('/admin/telemetry/overview')) return overviewPayload
      return {}
    })

    await act(async () => {
      renderDashboard()
    })
    await waitFor(() => expect(screen.getByText('Chat turns')).toBeInTheDocument())

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Session drill-down/i }))
    })

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Search' }))
    })

    await waitFor(() => {
      expect(screen.getByText(/Enter a session_id or turn_id/i)).toBeInTheDocument()
    })
  })
})
