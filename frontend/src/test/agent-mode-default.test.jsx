/**
 * Agent mode default-on (issue #849).
 *
 * Covers: agent mode starts enabled when the browser has no stored choice,
 * an explicitly stored "off" preference is honored, and the stored choice
 * wins on every read.
 */
import { renderHook, act } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import { useAgentMode } from '../hooks/chat/useAgentMode'

describe('useAgentMode default (issue #849)', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('defaults to enabled when nothing is stored', () => {
    const { result } = renderHook(() => useAgentMode(true))
    expect(result.current.agentModeEnabled).toBe(true)
  })

  it('honors a stored disabled preference', () => {
    localStorage.setItem('chatui-agent-mode-enabled', 'false')
    const { result } = renderHook(() => useAgentMode(true))
    expect(result.current.agentModeEnabled).toBe(false)
  })

  it('honors a stored enabled preference', () => {
    localStorage.setItem('chatui-agent-mode-enabled', 'true')
    const { result } = renderHook(() => useAgentMode(true))
    expect(result.current.agentModeEnabled).toBe(true)
  })

  it('a stored disabled preference survives rerenders', () => {
    localStorage.setItem('chatui-agent-mode-enabled', 'false')
    const { result, rerender } = renderHook(() => useAgentMode(true))
    rerender()
    expect(result.current.agentModeEnabled).toBe(false)
  })

  it('still force-disables when the feature becomes unavailable', () => {
    const { result, rerender } = renderHook(({ available }) => useAgentMode(available), {
      initialProps: { available: true },
    })
    expect(result.current.agentModeEnabled).toBe(true)

    act(() => {
      rerender({ available: false })
    })
    expect(result.current.agentModeEnabled).toBe(false)
    expect(localStorage.getItem('chatui-agent-mode-enabled')).toBe('false')
  })
})