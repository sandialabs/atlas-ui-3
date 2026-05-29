import { describe, it, expect } from 'vitest'
import {
  buildPromptInfoByKey,
  resolvePromptInfo,
  buildExportConversation,
} from '../utils/chatExport'

const PROMPTS_CONFIG = [
  {
    server: 'prompts',
    prompts: [
      { name: 'intel_analyst', description: 'Acts as an intelligence analyst' },
      { name: 'plain_summary' },
    ],
  },
  {
    server: 'work_helper',
    prompts: [
      { name: 'pirate_voice', description: 'Talk like a pirate' },
    ],
  },
]

describe('chatExport.buildPromptInfoByKey', () => {
  it('keys prompts by server_name and preserves description/server', () => {
    const lookup = buildPromptInfoByKey(PROMPTS_CONFIG)
    expect(lookup.prompts_intel_analyst).toEqual({
      key: 'prompts_intel_analyst',
      name: 'intel_analyst',
      description: 'Acts as an intelligence analyst',
      server: 'prompts',
    })
    expect(lookup.work_helper_pirate_voice.server).toBe('work_helper')
    expect(lookup.prompts_plain_summary.description).toBe('')
  })

  it('handles empty/missing input', () => {
    expect(buildPromptInfoByKey(undefined)).toEqual({})
    expect(buildPromptInfoByKey([])).toEqual({})
    expect(buildPromptInfoByKey([{ server: 'x' }])).toEqual({})
  })
})

describe('chatExport.resolvePromptInfo', () => {
  const lookup = buildPromptInfoByKey(PROMPTS_CONFIG)

  it('returns null for null/empty key', () => {
    expect(resolvePromptInfo(null, lookup)).toBeNull()
    expect(resolvePromptInfo('', lookup)).toBeNull()
  })

  it('resolves a known key', () => {
    expect(resolvePromptInfo('prompts_intel_analyst', lookup).name).toBe('intel_analyst')
  })

  it('falls back to a stub for unknown keys', () => {
    expect(resolvePromptInfo('gone_server_gone_prompt', lookup)).toEqual({
      key: 'gone_server_gone_prompt',
      name: 'gone_server_gone_prompt',
      description: '',
      server: '',
    })
  })
})

describe('chatExport.buildExportConversation', () => {
  const lookup = buildPromptInfoByKey(PROMPTS_CONFIG)

  it('passes messages through unchanged when none carry a prompt snapshot', () => {
    const messages = [
      { role: 'user', content: 'hi', timestamp: '2026-05-08T00:00:00Z' },
      { role: 'assistant', content: 'hello', timestamp: '2026-05-08T00:00:01Z' },
    ]
    expect(buildExportConversation(messages, lookup)).toEqual(messages)
  })

  it('injects a system entry when a custom prompt is first activated', () => {
    const messages = [
      { role: 'user', content: 'plain', timestamp: 't0', _activePromptKey: null },
      { role: 'assistant', content: 'reply', timestamp: 't0a' },
      { role: 'user', content: 'with prompt', timestamp: 't1', _activePromptKey: 'prompts_intel_analyst' },
      { role: 'assistant', content: 'analyst reply', timestamp: 't1a' },
    ]
    const out = buildExportConversation(messages, lookup)
    expect(out).toHaveLength(5)
    const sys = out[2]
    expect(sys.role).toBe('system')
    expect(sys._promptChange).toBe(true)
    expect(sys.promptKey).toBe('prompts_intel_analyst')
    expect(sys.promptName).toBe('intel_analyst')
    expect(sys.promptServer).toBe('prompts')
    expect(sys.content).toContain('intel_analyst')
    expect(sys.content).toContain('Acts as an intelligence analyst')
    expect(sys.timestamp).toBe('t1')
  })

  it('strips _activePromptKey from exported messages', () => {
    const messages = [
      { role: 'user', content: 'hi', _activePromptKey: 'prompts_intel_analyst', timestamp: 't0' },
    ]
    const out = buildExportConversation(messages, lookup)
    out.forEach(m => expect(m).not.toHaveProperty('_activePromptKey'))
  })

  it('injects a system entry when switching from one custom prompt to another', () => {
    const messages = [
      { role: 'user', content: 'a', timestamp: 't0', _activePromptKey: 'prompts_intel_analyst' },
      { role: 'assistant', content: 'r', timestamp: 't0a' },
      { role: 'user', content: 'b', timestamp: 't1', _activePromptKey: 'work_helper_pirate_voice' },
    ]
    const out = buildExportConversation(messages, lookup)
    const changes = out.filter(m => m._promptChange)
    expect(changes).toHaveLength(2)
    expect(changes[0].promptKey).toBe('prompts_intel_analyst')
    expect(changes[1].promptKey).toBe('work_helper_pirate_voice')
  })

  it('emits a "cleared" entry when switching from custom back to default', () => {
    const messages = [
      { role: 'user', content: 'a', timestamp: 't0', _activePromptKey: 'prompts_intel_analyst' },
      { role: 'user', content: 'b', timestamp: 't1', _activePromptKey: null },
    ]
    const out = buildExportConversation(messages, lookup)
    const cleared = out.find(m => m._promptChange && m.promptKey === null)
    expect(cleared).toBeDefined()
    expect(cleared.content).toMatch(/cleared/i)
  })

  it('does not emit a "cleared" entry when the conversation simply starts on default', () => {
    const messages = [
      { role: 'user', content: 'a', timestamp: 't0', _activePromptKey: null },
      { role: 'user', content: 'b', timestamp: 't1', _activePromptKey: null },
    ]
    const out = buildExportConversation(messages, lookup)
    expect(out.find(m => m._promptChange)).toBeUndefined()
  })

  it('falls back to a stub for prompts no longer present in config', () => {
    const messages = [
      { role: 'user', content: 'a', timestamp: 't0', _activePromptKey: 'gone_server_gone_prompt' },
    ]
    const out = buildExportConversation(messages, lookup)
    const sys = out.find(m => m._promptChange)
    expect(sys.promptName).toBe('gone_server_gone_prompt')
    expect(sys.promptServer).toBe('')
  })
})
