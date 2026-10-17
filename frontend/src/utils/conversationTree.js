/**
 * Arranges the flat conversation list into an expandable parent/child tree
 * (issue #1081).
 *
 * The relationship is an id only: a conversation may carry
 * `parent_conversation_id`, either from its saved metadata (durable) or from a
 * live run record (a child launched by `atlas_launch` that has not been saved
 * yet). The backend only publishes a parent id when the parent belongs to the
 * same user, so the UI never has to decide whether a link is allowed.
 *
 * The result stays a flat array, in tree order, with display fields attached:
 * `_depth`, `_childCount`, `_hasChildren`, `_ancestorIds` and `_aggregateRun`.
 * Keeping it flat means the sidebar still renders one row per conversation --
 * search, tags, context-menu actions and selection need no special casing --
 * and collapsing is a render-time visibility decision, never a change to the
 * conversation list or to a running agent.
 */

// Higher wins when several runs are folded into one collapsed-parent marker:
// a child that needs the user must not hide behind a sibling that is merely
// running, and a failure must not hide behind a stop.
const STATUS_PRIORITY = {
  waiting_for_input: 5,
  running: 4,
  queued: 3,
  failed: 2,
  cancelled: 1,
}

function priorityOf(status) {
  return STATUS_PRIORITY[status] || 0
}

function effectiveParentId(row, runsByConversation) {
  const liveRun = runsByConversation?.[row.id]
  return row?.parent_conversation_id || liveRun?.parent_conversation_id || null
}

/**
 * Whether following `id`'s parent chain revisits a row.
 *
 * A cycle is the one case the UI must break: malformed or hand-edited metadata
 * must render something rather than recurse forever. A *missing* ancestor is
 * not a reason to split the tree -- only the direct parent has to be present,
 * so a grandchild still nests under its own parent when the grandparent is
 * filtered out of the result.
 */
function hasParentCycle(id, parentById, presentIds) {
  let current = parentById.get(id)
  const seen = new Set([id])
  while (current && presentIds.has(current)) {
    if (seen.has(current)) return true
    seen.add(current)
    current = parentById.get(current)
  }
  return false
}

function aggregateRunFor(rowIndex, rows, runsByConversation) {
  const root = rows[rowIndex]
  let aggregate = null
  const consider = (run) => {
    const priority = priorityOf(run?.status)
    if (!priority) return
    if (!aggregate || priority > priorityOf(aggregate.status)) {
      aggregate = { status: run.status, count: 1, error: run.error || null }
    } else if (priority === priorityOf(aggregate.status)) {
      aggregate.count += 1
      if (!aggregate.error && run.error) aggregate.error = run.error
    }
  }

  // The parent's own run is part of the picture while it is collapsed: a
  // parent that is still working should keep its marker even when its child
  // rows are hidden.
  consider(runsByConversation?.[root.id])
  for (let i = rowIndex + 1; i < rows.length && rows[i]._depth > root._depth; i += 1) {
    consider(runsByConversation?.[rows[i].id])
  }
  return aggregate
}

export function arrangeConversations(rows, runsByConversation) {
  const list = Array.isArray(rows) ? rows : []
  const byId = new Map()
  list.forEach((row, index) => {
    if (row?.id && !byId.has(row.id)) byId.set(row.id, { row, index })
  })

  const parentById = new Map()
  for (const [id, { row }] of byId) {
    const parentId = effectiveParentId(row, runsByConversation)
    if (parentId && parentId !== id) parentById.set(id, parentId)
  }

  const presentIds = new Set(byId.keys())
  const childrenById = new Map()
  const roots = []
  for (const [id, entry] of byId) {
    const parentId = parentById.get(id)
    if (
      parentId
      && presentIds.has(parentId)
      && !hasParentCycle(id, parentById, presentIds)
    ) {
      if (!childrenById.has(parentId)) childrenById.set(parentId, [])
      childrenById.get(parentId).push(entry)
    } else {
      roots.push(entry)
    }
  }

  const byIndex = (a, b) => a.index - b.index
  roots.sort(byIndex)
  for (const children of childrenById.values()) children.sort(byIndex)

  const arranged = []
  const visit = (entry, depth, ancestors) => {
    const children = childrenById.get(entry.row.id) || []
    arranged.push({
      ...entry.row,
      _depth: depth,
      _childCount: children.length,
      _hasChildren: children.length > 0,
      _ancestorIds: ancestors,
    })
    for (const child of children) visit(child, depth + 1, [...ancestors, entry.row.id])
  }
  for (const root of roots) visit(root, 0, [])

  return arranged.map((row, index) => (
    row._hasChildren
      ? { ...row, _aggregateRun: aggregateRunFor(index, arranged, runsByConversation) }
      : row
  ))
}
