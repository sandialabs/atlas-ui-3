// Reconcile a conversation's execution snapshot into the messages on screen.
//
// The snapshot is the shared projection (issue #1122). A joined view and a
// reconnect do not see every tool frame, so the poll has to upsert rows by
// invocation identity and drop approval cards the run is no longer waiting on.
// An older revision is refused: the run owns the counter, and a replica that
// answers with a stale document must not replace a newer view.

const SLEEP_TOOLS = new Set(['atlas_sleep', 'atlas_agent_sleep'])
const TERMINAL_TOOL_STATUS = new Set(['completed', 'failed', 'interrupted', 'expired', 'rejected'])
const TERMINAL_RUN_STATUS = new Set(['completed', 'failed', 'cancelled'])

// A legacy row (no invocation id) can stand in for an identified invocation
// only while it is still open. A settled legacy call_0 is an earlier turn's
// row, and a new turn's call_0 must be added beside it, not written over it.
export function isOpenLegacyRow(row) {
  return !!row && !row.invocation_id && !TERMINAL_TOOL_STATUS.has(row.status)
}

export function isTerminalRun(execution) {
  return !!execution && TERMINAL_RUN_STATUS.has(execution.status)
}

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
    return isOpenLegacyRow(row) && !!data.tool_call_id && row.tool_call_id === data.tool_call_id
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

const PATCH_FIELDS = ['status', 'result', 'invocation_id', 'run_id', 'step', 'arguments', 'tool_name']

function sameValue(a, b) {
  if (a === b) return true
  if (a == null || b == null || typeof a !== 'object' || typeof b !== 'object') return false
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false
  }
}

// Only the fields the projection actually knows and that differ. A null
// result or an empty arguments object is "not known yet", not a value, and
// the projection has no server_name, so neither overwrites what the row has.
// Arguments compare structurally: every poll returns fresh objects.
function rowPatch(current, next) {
  const patch = {}
  PATCH_FIELDS.forEach((key) => {
    const value = next[key]
    if (value === undefined || value === null) return
    if (key === 'arguments' && typeof value === 'object' && Object.keys(value).length === 0) return
    if (!sameValue(current[key], value)) patch[key] = value
  })
  return Object.keys(patch).length > 0 ? patch : null
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

  // A finished run's rows are already in the stored transcript, with full
  // results. Its projection only shortens them, so it adds and patches nothing.
  const invocations = !isTerminalRun(execution) && Array.isArray(execution.invocations)
    ? execution.invocations
    : []
  const pending = Array.isArray(execution.pending_requests) ? execution.pending_requests : []
  const pendingKeys = new Set(pending.map(requestIdentity).filter(Boolean))
  const source = Array.isArray(messages) ? messages : []

  const byInvocation = new Map()
  const legacy = new Map()
  source.forEach((message, index) => {
    if (message.type !== 'tool_call' && message.role !== 'tool') return
    if (message.invocation_id) byInvocation.set(message.invocation_id, index)
    else if (message.tool_call_id && !legacy.has(message.tool_call_id) && isOpenLegacyRow(message)) {
      legacy.set(message.tool_call_id, index)
    }
  })

  // A call still waiting on its approval card has no row yet: a client that
  // stayed on the conversation shows only the card until the tool starts.
  const awaitingApproval = new Set(pending
    .filter(request => request.type === 'tool_approval_request' && request.invocation_id)
    .map(request => request.invocation_id))

  const patches = new Map()
  const appended = []
  invocations.forEach((invocation) => {
    if (
      awaitingApproval.has(invocation.invocation_id)
      && invocation.status === 'calling'
      && !byInvocation.has(invocation.invocation_id)
    ) return
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
    // A row that already settled keeps what it has. The projection can lag
    // the live frame, and its result is a shortened copy.
    if (TERMINAL_TOOL_STATUS.has(source[index].status)) return
    const patch = rowPatch(source[index], row)
    if (patch) patches.set(index, patch)
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
      next.push({ ...message, ...patch })
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
