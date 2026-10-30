// Reconcile a conversation's execution snapshot into the messages on screen.
//
// The snapshot is the shared projection (issue #1122). A joined view and a
// reconnect do not see every tool frame, so the poll has to upsert rows by
// invocation identity and drop approval cards the run is no longer waiting on.
// An older revision is refused: the run owns the counter, and a replica that
// answers with a stale document must not replace a newer view.

const SLEEP_TOOLS = new Set(['atlas_sleep', 'atlas_agent_sleep'])
const TERMINAL_TOOL_STATUS = new Set(['completed', 'failed', 'interrupted', 'expired', 'rejected'])

export function liveInvocationCount(messages, runId) {
  return (messages || []).filter(message => (
    message
    && message.type === 'tool_call'
    && message.invocation_id
    && (!runId || message.run_id === runId)
  )).length
}

export function isSleepTool(name) {
  return SLEEP_TOOLS.has(name)
}

export function requestIdentity(row) {
  if (!row) return null
  if (row.invocation_id) return `inv:${row.invocation_id}`
  if (row.elicitation_id) return `elicit:${row.elicitation_id}`
  if (row.tool_call_id) return `tool:${row.tool_call_id}`
  return null
}

// Tool rows match on invocation identity. A frame that carries one must not
// update a different invocation that reused the provider tool_call_id. A frame
// with only tool_call_id still matches a legacy row that has none.
export function sameInvocation(row, data) {
  if (!row || !data) return false
  if (data.invocation_id) {
    if (row.invocation_id) return row.invocation_id === data.invocation_id
    return !row.invocation_id && !!data.tool_call_id && row.tool_call_id === data.tool_call_id
  }
  if (row.invocation_id) return false
  return !!data.tool_call_id && row.tool_call_id === data.tool_call_id
}

export function snapshotSupersedes(incoming, previous) {
  if (typeof incoming !== 'number') return true
  if (typeof previous !== 'number') return true
  return incoming >= previous
}

function revisionState(previousRevision) {
  if (previousRevision && typeof previousRevision === 'object') {
    return {
      revision: typeof previousRevision.revision === 'number' ? previousRevision.revision : null,
      runId: previousRevision.run_id || null,
    }
  }
  return {
    revision: typeof previousRevision === 'number' ? previousRevision : null,
    runId: null,
  }
}

function invocationRow(invocation) {
  const name = invocation.tool_name || invocation.tool_call_id || 'tool'
  return {
    role: 'system',
    content: `**Tool Call: ${name}**`,
    type: 'tool_call',
    tool_call_id: invocation.tool_call_id,
    invocation_id: invocation.invocation_id,
    run_id: invocation.run_id,
    step: invocation.step,
    tool_name: invocation.tool_name,
    server_name: invocation.server_name || 'tool',
    arguments: invocation.arguments || {},
    result: invocation.result ?? null,
    status: invocation.status || 'calling',
    timestamp: new Date().toISOString(),
  }
}

function approvalRow(request) {
  return {
    role: 'system',
    content: `Tool Approval Required: ${request.tool_name || 'tool'}`,
    type: 'tool_approval_request',
    tool_call_id: request.tool_call_id,
    invocation_id: request.invocation_id,
    run_id: request.run_id,
    conversation_id: request.conversation_id,
    step: request.step,
    elicitation_id: request.elicitation_id,
    tool_name: request.tool_name,
    arguments: request.arguments || {},
    allow_edit: request.allow_edit !== false,
    admin_required: !!request.admin_required,
    status: 'pending',
    timestamp: new Date().toISOString(),
  }
}

function rowChanged(current, next) {
  return ['status', 'result', 'invocation_id', 'arguments', 'tool_name'].some(
    (key) => next[key] !== undefined && current[key] !== next[key]
  )
}

/**
 * Apply `execution` onto `messages`.
 *
 * Returns the same array when nothing changed. Returns null when the snapshot
 * is older than `previousRevision` (or has no numeric revision), so the caller
 * keeps the view it already has.
 */
export function reconcileExecution(messages, execution, previousRevision) {
  if (!execution || typeof execution.revision !== 'number') return null
  const previous = revisionState(previousRevision)
  const runChanged = previous.runId && execution.run_id && previous.runId !== execution.run_id
  if (!runChanged && !snapshotSupersedes(execution.revision, previous.revision)) return null

  const invocations = Array.isArray(execution.invocations) ? execution.invocations : []
  const pending = Array.isArray(execution.pending_requests) ? execution.pending_requests : []
  const pendingKeys = new Set(pending.map(requestIdentity).filter(Boolean))
  const source = Array.isArray(messages) ? messages : []

  const byInvocation = new Map()
  const legacy = new Map()
  source.forEach((message, index) => {
    if (message.type !== 'tool_call' && message.role !== 'tool') return
    if (message.invocation_id) byInvocation.set(message.invocation_id, index)
    else if (message.tool_call_id && !legacy.has(message.tool_call_id)) legacy.set(message.tool_call_id, index)
  })

  const patches = new Map()
  const appended = []
  invocations.forEach((invocation) => {
    let index = invocation.invocation_id ? byInvocation.get(invocation.invocation_id) : undefined
    if (index == null && invocation.tool_call_id && legacy.has(invocation.tool_call_id)) {
      index = legacy.get(invocation.tool_call_id)
      legacy.delete(invocation.tool_call_id)
    }
    const row = invocationRow(invocation)
    if (index == null) {
      appended.push(row)
      return
    }
    if (rowChanged(source[index], row)) patches.set(index, row)
  })

  const dropped = new Set()
  source.forEach((message, index) => {
    if (message.type !== 'tool_approval_request') return
    if (execution.run_id && message.run_id && message.run_id !== execution.run_id) return
    const key = requestIdentity(message)
    if (key && !pendingKeys.has(key)) dropped.add(index)
  })

  const present = new Set()
  source.forEach((message, index) => {
    if (message.type === 'tool_approval_request' && !dropped.has(index)) {
      const key = requestIdentity(message)
      if (key) present.add(key)
    }
  })
  const addedPending = pending
    .filter((request) => {
      const key = requestIdentity(request)
      return key && !present.has(key) && (request.type === 'tool_approval_request' || !request.type)
    })
    .map(approvalRow)

  if (patches.size === 0 && appended.length === 0 && dropped.size === 0 && addedPending.length === 0) {
    return source
  }

  const next = []
  source.forEach((message, index) => {
    if (dropped.has(index)) return
    if (patches.has(index)) {
      const patch = patches.get(index)
      next.push({
        ...message,
        ...patch,
        role: message.role || patch.role,
        timestamp: message.timestamp || patch.timestamp,
        content: message.content || patch.content,
      })
      return
    }
    next.push(message)
  })
  return next.concat(appended, addedPending)
}

export function toolStatusFromEvent(data, fallback) {
  if (data && TERMINAL_TOOL_STATUS.has(data.status)) return data.status
  return fallback
}
