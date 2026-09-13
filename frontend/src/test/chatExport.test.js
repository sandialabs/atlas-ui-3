import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  buildPromptInfoByKey,
  resolvePromptInfo,
  buildExportConversation,
  buildPersistedMessage,
  formatToolCallForText,
  openBlobInNewTab,
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
      preview: '',
    })
    expect(lookup.work_helper_pirate_voice.server).toBe('work_helper')
    expect(lookup.prompts_plain_summary.description).toBe('')
  })

  it('handles empty/missing input', () => {
    expect(buildPromptInfoByKey(undefined)).toEqual({})
    expect(buildPromptInfoByKey([])).toEqual({})
    expect(buildPromptInfoByKey([{ server: 'x' }])).toEqual({})
  })

  it('includes user-authored prompts keyed under userprompt: with a body preview', () => {
    const lookup = buildPromptInfoByKey(PROMPTS_CONFIG, [
      { id: 'abc', title: 'My Helper', content: 'Line 1\nLine 2\nLine 3\nLine 4\nLine 5\nLine 6\nLine 7' },
    ])
    expect(lookup['userprompt:abc']).toBeDefined()
    expect(lookup['userprompt:abc'].name).toBe('My Helper')
    expect(lookup['userprompt:abc'].server).toBe('user library')
    // First 5 lines + truncation marker, but not lines 6/7
    expect(lookup['userprompt:abc'].preview).toContain('Line 1')
    expect(lookup['userprompt:abc'].preview).toContain('Line 5')
    expect(lookup['userprompt:abc'].preview).not.toContain('Line 6')
    expect(lookup['userprompt:abc'].preview).toMatch(/…$/)
  })

  it('does not add a truncation marker when the body fits in the preview', () => {
    const lookup = buildPromptInfoByKey([], [
      { id: 'x', title: 'Short', content: 'just one line' },
    ])
    expect(lookup['userprompt:x'].preview).toBe('just one line')
  })

  it('skips user prompts without an id', () => {
    const lookup = buildPromptInfoByKey([], [{ title: 'no id', content: 'hi' }])
    expect(lookup).toEqual({})
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
      preview: '',
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

  it('includes name and body preview for user-authored prompts', () => {
    const userLookup = buildPromptInfoByKey(PROMPTS_CONFIG, [
      { id: 'u1', title: 'My Helper', content: 'You are a helpful assistant.\nAlways cite sources.\nNever invent URLs.' },
    ])
    const messages = [
      { role: 'user', content: 'hi', timestamp: 't0', _activePromptKey: 'userprompt:u1' },
    ]
    const out = buildExportConversation(messages, userLookup)
    const sys = out.find(m => m._promptChange)
    expect(sys.promptName).toBe('My Helper')
    expect(sys.promptServer).toBe('user library')
    expect(sys.promptPreview).toContain('You are a helpful assistant.')
    expect(sys.content).toContain('Custom prompt activated: My Helper')
    expect(sys.content).toContain('Prompt preview:')
    expect(sys.content).toContain('Always cite sources.')
  })
})

describe('chatExport.buildPersistedMessage', () => {
  it('keeps core fields for a normal message and adds no metadata', () => {
    const out = buildPersistedMessage({ role: 'assistant', content: 'hello', timestamp: 't1' })
    expect(out).toEqual({
      role: 'assistant',
      content: 'hello',
      timestamp: 't1',
      message_type: 'chat',
    })
    expect(out.metadata).toBeUndefined()
  })

  it('tucks tool-call fields into metadata so they survive a reload (issue #684)', () => {
    const live = {
      role: 'system',
      content: '**Tool Call: calc_add**',
      timestamp: 't2',
      type: 'tool_call',
      tool_call_id: 'tc1',
      tool_name: 'calc_add',
      server_name: 'calc',
      arguments: { a: 1, b: 2 },
      result: '3',
      status: 'completed',
    }
    const out = buildPersistedMessage(live)
    expect(out.message_type).toBe('tool_call')
    expect(out.metadata).toEqual({
      tool_call_id: 'tc1',
      tool_name: 'calc_add',
      server_name: 'calc',
      arguments: { a: 1, b: 2 },
      result: '3',
      status: 'completed',
    })
  })

  it('omits undefined tool fields from metadata', () => {
    const out = buildPersistedMessage({ role: 'system', type: 'tool_call', tool_name: 't' })
    expect(out.metadata).toEqual({ tool_name: 't' })
  })

  it('persists auto_approved and status on tool_approval_request so reloaded transcripts keep the flag', () => {
    // Without persisting these fields, a reloaded conversation lost the
    // auto-approval decision and re-rendered the row as if the setting were
    // still live — the restore path spreads `metadata` back onto the message,
    // so the flag must round-trip through `metadata`.
    const live = {
      role: 'system',
      content: 'Tool Approval Required: run_python',
      timestamp: 't3',
      type: 'tool_approval_request',
      tool_call_id: 'call_1',
      tool_name: 'run_python',
      arguments: { code: 'print(1)' },
      allow_edit: true,
      admin_required: false,
      status: 'completed',
      auto_approved: true,
    }
    const out = buildPersistedMessage(live)
    expect(out.message_type).toBe('tool_approval_request')
    expect(out.metadata).toEqual({
      tool_call_id: 'call_1',
      tool_name: 'run_python',
      arguments: { code: 'print(1)' },
      allow_edit: true,
      admin_required: false,
      status: 'completed',
      auto_approved: true,
    })
  })

  it('omits undefined approval fields from metadata', () => {
    const out = buildPersistedMessage({
      role: 'system',
      type: 'tool_approval_request',
      tool_call_id: 'call_1',
      tool_name: 'run_python',
      status: 'pending',
    })
    expect(out.metadata).toEqual({
      tool_call_id: 'call_1',
      tool_name: 'run_python',
      status: 'pending',
    })
    // auto_approved is the whole point: it must not appear when unset, so the
    // restore path doesn't spread a stale `false` over a row that was never
    // decided.
    expect(out.metadata).not.toHaveProperty('auto_approved')
  })

  it('preserves rejection_reason on a rejected approval row', () => {
    const out = buildPersistedMessage({
      role: 'system',
      type: 'tool_approval_request',
      tool_call_id: 'call_1',
      tool_name: 'run_python',
      status: 'rejected',
      auto_approved: false,
      rejection_reason: 'looks unsafe',
    })
    expect(out.metadata).toEqual({
      tool_call_id: 'call_1',
      tool_name: 'run_python',
      status: 'rejected',
      auto_approved: false,
      rejection_reason: 'looks unsafe',
    })
  })
})

describe('chatExport.formatToolCallForText', () => {
  it('returns null for non tool-call messages', () => {
    expect(formatToolCallForText({ role: 'user', content: 'hi' })).toBeNull()
    expect(formatToolCallForText(null)).toBeNull()
  })

  it('renders tool name, input arguments and output result', () => {
    const block = formatToolCallForText({
      type: 'tool_call',
      tool_name: 'calc_add',
      server_name: 'calc',
      status: 'completed',
      arguments: { a: 1, b: 2 },
      result: '3',
    })
    expect(block).toContain('TOOL CALL: calc_add (calc)')
    expect(block).toContain('Status: completed')
    expect(block).toContain('Input Arguments:')
    expect(block).toContain('"a": 1')
    expect(block).toContain('Output Result:')
    expect(block).toContain('3')
  })

  it('elides large base64 file data in arguments', () => {
    const block = formatToolCallForText({
      type: 'tool_call',
      tool_name: 'upload',
      arguments: { file_data_base64: 'x'.repeat(500) },
    })
    expect(block).toContain('hidden for display')
    expect(block).not.toContain('x'.repeat(500))
  })
})

// Issue #908: transcript exports open in a new browser tab by default. jsdom
// has no blob URL store or window.open, so these stub both and pin the
// contract: open the blob URL in a new tab, fall back to a file download when
// the popup is blocked, and revoke the URL only after the tab has had time to
// load it. Fake timers run for the whole describe: the helper schedules a
// 60-second revoke timer, and a real one left behind by a test would keep the
// vitest worker alive and could fire after the mocks are restored.
describe('chatExport.openBlobInNewTab', () => {
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL
  const originalWindowOpen = window.open

  const createdAnchors = []
  let createObjectURL
  let revokeObjectURL
  let windowOpen

  beforeEach(() => {
    vi.useFakeTimers()
    createdAnchors.length = 0
    createObjectURL = vi.fn(() => 'blob:mock-url')
    revokeObjectURL = vi.fn()
    windowOpen = vi.fn()
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL
    window.open = windowOpen
    const nativeCreateElement = document.createElement.bind(document)
    vi.spyOn(document, 'createElement').mockImplementation((tag, options) => {
      const el = nativeCreateElement(tag, options)
      if (tag === 'a') createdAnchors.push(el)
      return el
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
    window.open = originalWindowOpen
    vi.restoreAllMocks()
    document.body.innerHTML = ''
  })

  it('opens a new tab on the blob URL and does not revoke immediately', () => {
    const blob = new Blob(['hello'], { type: 'text/plain' })
    windowOpen.mockReturnValue({})

    openBlobInNewTab(blob, 'chat-export-2026.txt')

    expect(createObjectURL).toHaveBeenCalledWith(blob)
    expect(windowOpen).toHaveBeenCalledTimes(1)
    expect(windowOpen).toHaveBeenCalledWith('blob:mock-url', '_blank')
    expect(revokeObjectURL).not.toHaveBeenCalled()
    // No download anchor is created when the tab opened.
    expect(createdAnchors).toHaveLength(0)
  })

  it('falls back to a download with the given filename when the popup is blocked', () => {
    windowOpen.mockReturnValue(null)
    // jsdom has no navigation engine, so the fallback anchor's click would only
    // print a "Not implemented" warning; intercept it and assert on the call.
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    openBlobInNewTab(new Blob(['{}'], { type: 'application/json' }), 'chat-export-2026.json')

    expect(click).toHaveBeenCalledTimes(1)
    expect(createdAnchors).toHaveLength(1)
    const a = createdAnchors[0]
    expect(a.download).toBe('chat-export-2026.json')
    expect(a.href).toContain('blob:mock-url')
    // The temporary anchor is cleaned up out of the DOM after the click.
    expect(document.body.querySelector('a')).toBeNull()
  })

  it('still falls back to a download when window.open throws instead of returning null', () => {
    windowOpen.mockImplementation(() => { throw new Error('popup blocked') })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    openBlobInNewTab(new Blob(['x'], { type: 'text/plain' }), 'chat-export-2026.txt')

    expect(click).toHaveBeenCalledTimes(1)
    expect(createdAnchors).toHaveLength(1)
    expect(createdAnchors[0].download).toBe('chat-export-2026.txt')
  })

  it('revokes the blob URL after the tab has had time to load it', () => {
    windowOpen.mockReturnValue({})

    openBlobInNewTab(new Blob(['x'], { type: 'text/plain' }), 'chat-export-2026.txt')

    vi.advanceTimersByTime(59999)
    expect(revokeObjectURL).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
  })
})
