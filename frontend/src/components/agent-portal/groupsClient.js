// Thin client for /api/agent-portal/groups* (and /bundles, /audit
// later). Same pattern as portalStateClient: fire-and-forget HTTP,
// callers re-fetch the list to reflect successes.

async function jsonOrNull(res) {
  if (!res || !res.ok) return null
  try { return await res.json() } catch { return null }
}

export async function listGroups() {
  try {
    const res = await fetch('/api/agent-portal/groups', { credentials: 'include' })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.groups) ? body.groups : []
  } catch {
    return []
  }
}

export async function createGroup({ name, max_panes, mem_budget_bytes, cpu_budget_pct, idle_kill_seconds }) {
  try {
    const res = await fetch('/api/agent-portal/groups', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, max_panes, mem_budget_bytes, cpu_budget_pct, idle_kill_seconds }),
    })
    return await jsonOrNull(res)
  } catch {
    return null
  }
}

export async function deleteGroup(groupId) {
  try {
    const res = await fetch(`/api/agent-portal/groups/${encodeURIComponent(groupId)}`, {
      method: 'DELETE',
      credentials: 'include',
    })
    return res.ok
  } catch {
    return false
  }
}

export async function cancelGroup(groupId) {
  try {
    const res = await fetch(`/api/agent-portal/groups/${encodeURIComponent(groupId)}/cancel`, {
      method: 'POST',
      credentials: 'include',
    })
    return await jsonOrNull(res)
  } catch {
    return null
  }
}

export async function listAudit(limit = 200) {
  try {
    const res = await fetch(`/api/agent-portal/audit?limit=${encodeURIComponent(limit)}`, {
      credentials: 'include',
    })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.events) ? body.events : []
  } catch {
    return []
  }
}

export async function listBundles() {
  try {
    const res = await fetch('/api/agent-portal/bundles', { credentials: 'include' })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.bundles) ? body.bundles : []
  } catch {
    return []
  }
}

export async function launchBundle(bundleId) {
  try {
    const res = await fetch(`/api/agent-portal/bundles/${encodeURIComponent(bundleId)}/launch`, {
      method: 'POST',
      credentials: 'include',
    })
    return await jsonOrNull(res)
  } catch {
    return null
  }
}

export async function pauseGroup(groupId) {
  try {
    const res = await fetch(`/api/agent-portal/groups/${encodeURIComponent(groupId)}/pause`, {
      method: 'POST',
      credentials: 'include',
    })
    return await jsonOrNull(res)
  } catch {
    return null
  }
}

export async function resumeGroup(groupId) {
  try {
    const res = await fetch(`/api/agent-portal/groups/${encodeURIComponent(groupId)}/resume`, {
      method: 'POST',
      credentials: 'include',
    })
    return await jsonOrNull(res)
  } catch {
    return null
  }
}

export async function snapshotGroup(groupId) {
  try {
    const res = await fetch(`/api/agent-portal/groups/${encodeURIComponent(groupId)}/snapshot`, {
      credentials: 'include',
    })
    return await jsonOrNull(res)
  } catch {
    return null
  }
}
