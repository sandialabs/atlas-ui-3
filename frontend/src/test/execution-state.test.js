import { describe, expect, it } from 'vitest'

import { liveInvocationCount, reconcileExecution, sameInvocation, toolStatusFromEvent } from '../utils/executionState'

function invocation(id, overrides = {}) {
  return {
    invocation_id: id,
    run_id: 'run-1',
    step: 0,
    tool_call_id: id.split(':').pop(),
    tool_name: 'search',
    status: 'in_progress',
    ...overrides,
  }
}

describe('reconcileExecution', () => {
  it('shows every invocation of a parallel step before the step flushes', () => {
    const invocations = Array.from({ length: 10 }, (_, index) => invocation(`run-1:0:call_${index}`, {
      tool_call_id: `call_${index}`,
      tool_name: `tool_${index}`,
      status: index === 1 ? 'completed' : 'in_progress',
    }))
    const next = reconcileExecution([], {
      revision: 10,
      run_id: 'run-1',
      invocations,
      pending_requests: [{
        type: 'tool_approval_request',
        tool_call_id: 'call_3',
        invocation_id: 'run-1:0:call_3',
        run_id: 'run-1',
        tool_name: 'tool_3',
      }],
    }, null)
    expect(next.filter(row => row.type === 'tool_call')).toHaveLength(10)
    expect(next.filter(row => row.type === 'tool_approval_request')).toHaveLength(1)
    expect(next.find(row => row.tool_call_id === 'call_1').status).toBe('completed')
  })

  it('refuses an older revision of the same run and accepts a new run', () => {
    const current = [{ type: 'tool_call', invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0', status: 'completed' }]
    const older = reconcileExecution(current, {
      revision: 2,
      run_id: 'run-1',
      invocations: [invocation('run-1:0:call_0', { status: 'calling' })],
    }, { revision: 4, run_id: 'run-1' })
    expect(older).toBeNull()

    const replaced = reconcileExecution(current, {
      revision: 1,
      run_id: 'run-2',
      invocations: [invocation('run-2:0:call_0', { run_id: 'run-2', status: 'in_progress' })],
    }, { revision: 4, run_id: 'run-1' })
    expect(replaced.some(row => row.invocation_id === 'run-2:0:call_0')).toBe(true)
  })

  it('drops a settled approval card and keeps a card for another run', () => {
    const messages = [
      { type: 'tool_approval_request', invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0', run_id: 'run-1', status: 'pending' },
      { type: 'tool_approval_request', invocation_id: 'run-9:0:call_0', tool_call_id: 'call_0', run_id: 'run-9', status: 'pending' },
    ]
    const next = reconcileExecution(messages, {
      revision: 3,
      run_id: 'run-1',
      invocations: [invocation('run-1:0:call_0', { status: 'expired' })],
      pending_requests: [],
    }, 1)
    expect(next.some(row => row.run_id === 'run-1' && row.type === 'tool_approval_request')).toBe(false)
    expect(next.some(row => row.run_id === 'run-9' && row.type === 'tool_approval_request')).toBe(true)
    expect(next.find(row => row.invocation_id === 'run-1:0:call_0').status).toBe('expired')
  })

  it('keeps a completed tool beside a sleeping atlas_sleep row', () => {
    const next = reconcileExecution(
      [{ type: 'tool_call', invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0', status: 'completed', content: 'listed' }],
      {
        revision: 2,
        run_id: 'run-1',
        invocations: [
          invocation('run-1:0:call_0', { status: 'completed', result: 'listed' }),
          invocation('run-1:0:call_1', { tool_call_id: 'call_1', tool_name: 'atlas_sleep', status: 'sleeping' }),
        ],
        pending_requests: [],
      },
      1,
    )
    expect(next.find(row => row.invocation_id === 'run-1:0:call_0').status).toBe('completed')
    expect(next.find(row => row.tool_name === 'atlas_sleep').status).toBe('sleeping')
    expect(next.some(row => row.type === 'tool_approval_request')).toBe(false)
  })

  it('does not let a reused tool_call_id update a different invocation', () => {
    const messages = [
      { type: 'tool_call', invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0', status: 'completed' },
    ]
    const next = reconcileExecution(messages, {
      revision: 2,
      run_id: 'run-1',
      invocations: [
        invocation('run-1:0:call_0', { status: 'completed' }),
        invocation('run-1:1:call_0', { step: 1, status: 'in_progress' }),
      ],
    }, 1)
    expect(next.filter(row => row.tool_call_id === 'call_0')).toHaveLength(2)
    expect(next.find(row => row.invocation_id === 'run-1:0:call_0').status).toBe('completed')
    expect(next.find(row => row.invocation_id === 'run-1:1:call_0').status).toBe('in_progress')
  })

  it('returns the same array when the snapshot changes nothing', () => {
    const messages = [{ role: 'user', type: 'chat', content: 'hi' }]
    const next = reconcileExecution(messages, { revision: 1, run_id: 'run-1', invocations: [], pending_requests: [] }, 0)
    expect(next).toBe(messages)
  })
})

describe('reconcileExecution — settled rows and finished runs', () => {
  it('does not patch a settled row with a lagging or shortened projection copy', () => {
    const current = [{
      type: 'tool_call', invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0',
      run_id: 'run-1', status: 'completed', result: 'full result',
    }]
    const next = reconcileExecution(current, {
      revision: 3,
      run_id: 'run-1',
      status: 'running',
      invocations: [invocation('run-1:0:call_0', { status: 'in_progress', result: null })],
    }, { revision: 2, run_id: 'run-1' })
    expect(next).toBe(current)
  })

  it('adds no rows from a finished run but still drops its stale cards', () => {
    const current = [
      { type: 'tool_approval_request', invocation_id: 'run-1:1:call_0', tool_call_id: 'call_0', run_id: 'run-1', status: 'pending' },
    ]
    const next = reconcileExecution(current, {
      revision: 9,
      run_id: 'run-1',
      status: 'cancelled',
      invocations: [invocation('run-1:0:call_9', { status: 'interrupted' })],
      pending_requests: [],
    }, null)
    expect(next).toEqual([])
  })
})

describe('reconcileExecution — legacy rows from earlier turns', () => {
  it('appends a new invocation instead of claiming a settled legacy call_0', () => {
    const current = [{ type: 'tool_call', tool_call_id: 'call_0', status: 'completed', result: 'old turn' }]
    const next = reconcileExecution(current, {
      revision: 1,
      run_id: 'run-2',
      status: 'running',
      invocations: [invocation('run-2:0:call_0', { run_id: 'run-2', status: 'expired' })],
    }, null)
    expect(next).toHaveLength(2)
    expect(next[0]).toBe(current[0])
    expect(next[1].invocation_id).toBe('run-2:0:call_0')
  })
})

describe('invocation identity helpers', () => {
  it('matches a legacy row only when the frame has no invocation id', () => {
    const legacy = { tool_call_id: 'call_0' }
    const exact = { invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0' }
    expect(sameInvocation(legacy, { tool_call_id: 'call_0' })).toBe(true)
    expect(sameInvocation(exact, { invocation_id: 'run-1:1:call_0', tool_call_id: 'call_0' })).toBe(false)
    expect(sameInvocation(legacy, { invocation_id: 'run-1:0:call_0', tool_call_id: 'call_0' })).toBe(true)
  })

  it('counts live rows for the snapshot run only', () => {
    const messages = [
      { type: 'tool_call', invocation_id: 'run-1:0:call_0', run_id: 'run-1' },
      { type: 'tool_call', invocation_id: 'run-1:0:call_1', run_id: 'run-1' },
      { type: 'tool_call', invocation_id: 'run-2:0:call_0', run_id: 'run-2' },
      { type: 'tool_approval_request', invocation_id: 'run-2:0:call_1', run_id: 'run-2' },
    ]
    expect(liveInvocationCount(messages, 'run-2')).toBe(1)
    expect(liveInvocationCount(messages, 'run-1')).toBe(2)
  })

  it('keeps expired and rejected as their own statuses', () => {
    expect(toolStatusFromEvent({ status: 'expired' }, 'failed')).toBe('expired')
    expect(toolStatusFromEvent({ status: 'rejected' }, 'failed')).toBe('rejected')
    expect(toolStatusFromEvent({}, 'failed')).toBe('failed')
  })
})
