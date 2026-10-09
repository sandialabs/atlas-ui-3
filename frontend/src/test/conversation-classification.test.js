/**
 * Saved conversations open only under the classification they were recorded
 * at (issue #1042). The server enforces the rule; these pin the UI mirror that
 * labels sidebar rows and explains a refusal, and the fetch that asks the
 * server to check.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import {
  classificationOf,
  classificationRefusal,
  classificationLabel,
} from '../utils/conversationClassification'
import { useConversationHistory } from '../hooks/useConversationHistory'

const enforced = level => ({ complianceEnabled: true, activeLevel: level })
const disabled = { complianceEnabled: false, activeLevel: null }

describe('classificationOf', () => {
  it('reads server listing rows', () => {
    expect(classificationOf({ data_classification: 'CUI', data_classification_state: 'classified' }))
      .toEqual({ state: 'classified', level: 'CUI' })
  })

  it('reads local records from metadata', () => {
    expect(classificationOf({ metadata: { data_classification: 'CUI' } }).state).toBe('classified')
    expect(classificationOf({ metadata: { data_classification: null } }).state).toBe('unclassified')
    expect(classificationOf({ metadata: {} }).state).toBe('legacy')
    expect(classificationOf({ metadata: { data_classification: 5 } }).state).toBe('invalid')
  })

  it('leaves rows it cannot judge to the server', () => {
    expect(classificationOf({ id: 'run-row', _run: true }).state).toBe('unknown')
    expect(classificationRefusal({ id: 'run-row', _run: true }, enforced('UUR'))).toBeNull()
  })
})

describe('classificationRefusal', () => {
  const cui = { data_classification: 'CUI', data_classification_state: 'classified' }
  const none = { data_classification: null, data_classification_state: 'unclassified' }
  const legacy = { data_classification: null, data_classification_state: 'legacy' }

  it('allows only the recorded level', () => {
    expect(classificationRefusal(cui, enforced('CUI'))).toBeNull()
    expect(classificationRefusal(cui, enforced('UUR'))).toMatch(/saved under CUI/)
    expect(classificationRefusal(cui, enforced(null))).toBeTruthy()
    expect(classificationRefusal(cui, disabled)).toBeTruthy()
  })

  it('keeps unclassified conversations out of a classified context', () => {
    expect(classificationRefusal(none, enforced(null))).toBeNull()
    expect(classificationRefusal(none, enforced('UUR'))).toBeTruthy()
    expect(classificationRefusal(none, disabled)).toBeNull()
  })

  it('fails closed for legacy records while levels are enforced', () => {
    expect(classificationRefusal(legacy, enforced('UUR'))).toBeTruthy()
    expect(classificationRefusal(legacy, enforced(null))).toBeTruthy()
    expect(classificationRefusal(legacy, disabled)).toBeNull()
  })

  it('labels rows', () => {
    expect(classificationLabel(cui)).toBe('CUI')
    expect(classificationLabel(none)).toBeNull()
    expect(classificationLabel(legacy)).toBe('Unrecorded level')
  })
})

describe('useConversationHistory.loadConversation', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sends the active level and reports a 409 without content', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'saved under CUI', error_type: 'conversation_classification' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConversationHistory())
    let out
    await act(async () => {
      out = await result.current.loadConversation('c1', { complianceLevel: 'UUR' })
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/conversations/c1?compliance_level=UUR')
    expect(out).toEqual({ classificationRefused: true, message: 'saved under CUI' })
  })

  it('sends an empty level for "no level" and nothing when not asked', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ id: 'c1' }) })
    vi.stubGlobal('fetch', fetchMock)
    const { result } = renderHook(() => useConversationHistory())
    await act(async () => {
      await result.current.loadConversation('c1', { complianceLevel: '' })
      await result.current.loadConversation('c1')
    })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/conversations/c1?compliance_level=')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/conversations/c1')
  })
})

describe('alias resolution', () => {
  const levels = [{ name: 'CUI', aliases: ['CUI-Basic'] }, { name: 'UUR', aliases: [] }]
  it('matches a recorded level through its alias, as the server does', () => {
    const row = { data_classification: 'CUI', data_classification_state: 'classified' }
    expect(classificationRefusal(row, { complianceEnabled: true, activeLevel: 'CUI-Basic', levels })).toBeNull()
    const aliased = { metadata: { data_classification: 'CUI-Basic' } }
    expect(classificationRefusal(aliased, { complianceEnabled: true, activeLevel: 'CUI', levels })).toBeNull()
    expect(classificationRefusal(aliased, { complianceEnabled: true, activeLevel: 'UUR', levels })).toBeTruthy()
  })
})

describe('a refused steer frame', () => {
  it('reports the refusal without ending the running turn', async () => {
    const { createWebSocketHandler } = await import('../handlers/chat/websocketHandlers')
    const deps = {
      addMessage: vi.fn(), mapMessages: vi.fn(), setIsThinking: vi.fn(),
      setCurrentAgentStep: vi.fn(), streamToken: vi.fn(), streamEnd: vi.fn(),
    }
    const handler = createWebSocketHandler(deps)
    handler({ type: 'error', error_type: 'conversation_classification', steering: true, message: 'running under CUI' })
    expect(deps.addMessage).toHaveBeenCalledTimes(1)
    expect(deps.setIsThinking).not.toHaveBeenCalled()
    expect(deps.setCurrentAgentStep).not.toHaveBeenCalled()

    handler({ type: 'error', error_type: 'conversation_classification', message: 'saved under CUI' })
    expect(deps.setIsThinking).toHaveBeenCalledWith(false)
  })
})
