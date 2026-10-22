/**
 * Context meter and compaction controls (issue #877).
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, fireEvent, render, renderHook, screen } from '@testing-library/react'
import ContextUsageMeter from '../components/ContextUsageMeter'
import ContextCheckpointRow from '../components/ContextCheckpointRow'
import { parseCompactCommand, useContextCompaction } from '../hooks/chat/useContextCompaction'
import { createWebSocketHandler } from '../handlers/chat/websocketHandlers'
import { formatTokens, meterLevel } from '../utils/contextUsage'
import { DISPLAY_ONLY_MESSAGE_TYPES } from '../utils/chatExport'
import { STORED_ROW_TYPES, isLiveOnlyRow } from '../utils/transcriptAlignment'

const USAGE = {
  type: 'context_usage',
  estimated_tokens: 82000,
  estimate_method: 'tokenizer',
  context_window: 128000,
  window_source: 'litellm',
  usable_tokens: 115000,
  threshold_tokens: 92000,
  provider_last_prompt_tokens: 80500,
  cumulative_prompt_tokens: 400000,
  cumulative_completion_tokens: 12000,
  reported_calls: 9,
  checkpoints: 1,
}

describe('parseCompactCommand', () => {
  it('reads the command and its focus', () => {
    expect(parseCompactCommand('/compact')).toEqual({ focus: '' })
    expect(parseCompactCommand('  /compact focus on auth  ')).toEqual({ focus: 'focus on auth' })
  })
  it('ignores everything else', () => {
    expect(parseCompactCommand('/compactor x')).toBeNull()
    expect(parseCompactCommand('please /compact')).toBeNull()
    expect(parseCompactCommand(null)).toBeNull()
  })
})

describe('meter helpers', () => {
  it('formats token counts', () => {
    expect(formatTokens(82000)).toBe('82K')
    expect(formatTokens(1500000)).toBe('1.5M')
    expect(formatTokens(950)).toBe('950')
    expect(formatTokens(undefined)).toBe('?')
  })
  it('grades the level against threshold and usable', () => {
    expect(meterLevel({ ...USAGE, estimated_tokens: 1000 })).toBe('ok')
    expect(meterLevel({ ...USAGE, estimated_tokens: 70000 })).toBe('warn')
    expect(meterLevel({ ...USAGE, estimated_tokens: 93000 })).toBe('high')
    expect(meterLevel({ ...USAGE, estimated_tokens: 116000 })).toBe('over')
  })
})

describe('useContextCompaction', () => {
  beforeEach(() => localStorage.clear())

  it('sends a manual compaction with the request fields', () => {
    const sendMessage = vi.fn(() => true)
    const { result } = renderHook(() => useContextCompaction({
      config: { enabled: true, auto_default: true },
      activeConversationId: 'c1',
      sendMessage,
      buildRequestFields: () => ({ model: 'm', conversation_id: 'c1', compliance_level_filter: 'CUI' }),
    }))
    act(() => { result.current.compactNow('auth') })
    expect(sendMessage).toHaveBeenCalledWith({
      type: 'compact_context', focus: 'auth', auto_compact: true,
      model: 'm', conversation_id: 'c1', compliance_level_filter: 'CUI',
    })
    expect(result.current.isCompacting).toBe(true)
    act(() => { result.current.handleCompaction({ status: 'completed' }) })
    expect(result.current.isCompacting).toBe(false)
  })

  it('remembers the toggle per conversation', () => {
    const props = { config: { enabled: true, auto_default: true }, activeConversationId: 'c1', sendMessage: vi.fn() }
    const { result, rerender } = renderHook((p) => useContextCompaction(p), { initialProps: props })
    act(() => { result.current.setAutoCompact(false) })
    rerender({ ...props, activeConversationId: 'c2' })
    expect(result.current.autoCompact).toBe(true)
    rerender({ ...props, activeConversationId: 'c1' })
    expect(result.current.autoCompact).toBe(false)
  })

  it('carries an unsaved chat\'s choice to its new id', () => {
    const props = { config: { enabled: true, auto_default: true }, activeConversationId: null, sendMessage: vi.fn() }
    const { result, rerender } = renderHook((p) => useContextCompaction(p), { initialProps: props })
    act(() => { result.current.setAutoCompact(false) })
    rerender({ ...props, activeConversationId: 'new-id' })
    expect(result.current.autoCompact).toBe(false)
    expect(JSON.parse(localStorage.getItem('atlas.autoCompactByConversation'))['new-id']).toBe(false)
  })

  it('does nothing when the feature is off', () => {
    const sendMessage = vi.fn(() => true)
    const { result } = renderHook(() => useContextCompaction({
      config: { enabled: false }, activeConversationId: 'c1', sendMessage,
    }))
    expect(result.current.compactNow()).toBe(false)
    expect(result.current.requestUsage()).toBe(false)
    expect(sendMessage).not.toHaveBeenCalled()
  })

  it('clears the estimate when the conversation changes', () => {
    const props = { config: {}, activeConversationId: 'c1', sendMessage: vi.fn() }
    const { result, rerender } = renderHook((p) => useContextCompaction(p), { initialProps: props })
    act(() => { result.current.handleUsage(USAGE) })
    expect(result.current.usage).toEqual(USAGE)
    rerender({ ...props, activeConversationId: 'c2' })
    expect(result.current.usage).toBeNull()
  })
})

describe('websocket handler', () => {
  function handler(extra = {}) {
    const deps = {
      addMessage: vi.fn(), mapMessages: vi.fn(), setIsThinking: vi.fn(), setCurrentAgentStep: vi.fn(),
      onContextUsage: vi.fn(), onContextCompaction: vi.fn(), ...extra,
    }
    return { deps, handle: createWebSocketHandler(deps) }
  }

  it('routes usage frames', () => {
    const { deps, handle } = handler()
    handle(USAGE)
    expect(deps.onContextUsage).toHaveBeenCalledWith(USAGE)
    expect(deps.addMessage).not.toHaveBeenCalled()
  })

  it('adds the checkpoint row on completion', () => {
    const { deps, handle } = handler()
    handle({
      type: 'context_compaction', status: 'completed', scope: 'run', trigger: 'auto',
      content: 'Context compacted (automatic): ~90K -> ~20K tokens', summary: '## Goal',
      before_tokens: 90000, after_tokens: 20000,
    })
    const row = deps.addMessage.mock.calls[0][0]
    expect(row).toMatchObject({ role: 'system', type: 'context_checkpoint', content: 'Context compacted (automatic): ~90K -> ~20K tokens' })
    expect(row.context_checkpoint.summary).toBe('## Goal')
    expect(isLiveOnlyRow(row)).toBe(false)
  })

  it('reports failures and manual skips, not automatic skips', () => {
    const { deps, handle } = handler()
    handle({ type: 'context_compaction', status: 'skipped', trigger: 'auto', reason: 'Nothing to compact' })
    expect(deps.addMessage).not.toHaveBeenCalled()
    handle({ type: 'context_compaction', status: 'skipped', trigger: 'manual', reason: 'Nothing to compact: x.' })
    handle({ type: 'context_compaction', status: 'failed', trigger: 'auto', error: 'Context compaction failed: down' })
    expect(deps.addMessage.mock.calls.map(c => c[0].content)).toEqual([
      'Nothing to compact: x.', 'Context compaction failed: down',
    ])
  })
})

describe('row types', () => {
  it('treats checkpoints as stored, display-only rows', () => {
    expect(STORED_ROW_TYPES.has('context_checkpoint')).toBe(true)
    expect(DISPLAY_ONLY_MESSAGE_TYPES).toContain('context_checkpoint')
  })
})

describe('ContextUsageMeter', () => {
  function meter(overrides = {}) {
    const contextCompaction = {
      enabled: true, usage: USAGE, isCompacting: false, autoCompact: true,
      setAutoCompact: vi.fn(), compactNow: vi.fn(() => true), ...overrides,
    }
    render(<ContextUsageMeter contextCompaction={contextCompaction} />)
    return contextCompaction
  }

  it('renders nothing without an estimate', () => {
    meter({ usage: null })
    expect(screen.queryByTestId('context-usage-meter')).not.toBeInTheDocument()
  })

  it('labels the value as an estimate against the window', () => {
    meter()
    expect(screen.getByTestId('context-usage-meter')).toHaveTextContent('Context ~82K / 128K')
  })

  it('marks an assumed window', () => {
    meter({ usage: { ...USAGE, window_source: 'fallback' } })
    expect(screen.getByTestId('context-usage-meter')).toHaveTextContent('/ 128K?')
    fireEvent.click(screen.getByTestId('context-usage-meter'))
    expect(screen.getByRole('dialog')).toHaveTextContent(/assumed/)
  })

  it('separates estimate, provider-reported and cumulative usage', () => {
    meter()
    fireEvent.click(screen.getByTestId('context-usage-meter'))
    const dialog = screen.getByRole('dialog')
    expect(dialog).toHaveTextContent('tokenizer estimate')
    expect(dialog).toHaveTextContent('reported by the provider')
    expect(dialog).toHaveTextContent('cumulative')
  })

  it('compacts with a focus and toggles auto', () => {
    const cc = meter()
    fireEvent.click(screen.getByTestId('context-usage-meter'))
    fireEvent.click(screen.getByTestId('auto-compact-toggle'))
    expect(cc.setAutoCompact).toHaveBeenCalledWith(false)
    fireEvent.change(screen.getByPlaceholderText('What to keep in most detail'), { target: { value: ' decisions ' } })
    fireEvent.click(screen.getByTestId('compact-now'))
    expect(cc.compactNow).toHaveBeenCalledWith('decisions')
  })

  it('disables Compact while busy', () => {
    render(<ContextUsageMeter contextCompaction={{ enabled: true, usage: USAGE, compactNow: vi.fn(), setAutoCompact: vi.fn() }} disabled />)
    fireEvent.click(screen.getByTestId('context-usage-meter'))
    expect(screen.getByTestId('compact-now')).toBeDisabled()
  })
})

describe('ContextCheckpointRow', () => {
  it('expands to the checkpoint summary', () => {
    render(<ContextCheckpointRow message={{
      content: 'Context compacted (manual): ~50K -> ~8K tokens',
      context_checkpoint: { scope: 'conversation', summary: '## Goal\nShip it' },
    }} />)
    expect(screen.getByTestId('context-checkpoint-row')).toHaveTextContent('~50K -> ~8K')
    fireEvent.click(screen.getByText('Show checkpoint'))
    expect(screen.getByText(/Ship it/)).toBeInTheDocument()
  })
})
