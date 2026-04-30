// Thin client around the /api/agent-portal/state/* endpoints.
//
// Every call is fire-and-forget from the caller's perspective: success
// is reflected by a refreshed local state, and failures are logged but
// never block the UI. The localStorage layer is kept as a first-paint
// cache only; the server is the source of truth.

const LAUNCH_HISTORY_KEY = 'atlas.agentPortal.launchHistory.v1'
const LAUNCH_CONFIGS_KEY = 'atlas.agentPortal.launchConfigs.v1'
const LAYOUT_KEY = 'atlas.agentPortal.layout.v1'

async function jsonOrNull(res) {
  if (!res || !res.ok) return null
  try { return await res.json() } catch { return null }
}

// ---------- Layout ---------------------------------------------------------

export function loadLayoutFromCache() {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

export function saveLayoutToCache(layout) {
  try {
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout))
  } catch {
    // localStorage may be full / disabled; ignore.
  }
}

export async function fetchLayoutFromServer() {
  try {
    const res = await fetch('/api/agent-portal/state/layout', { credentials: 'include' })
    const body = await jsonOrNull(res)
    if (!body) return null
    const layout = body.layout
    return layout && typeof layout === 'object' && Object.keys(layout).length > 0
      ? layout
      : null
  } catch {
    return null
  }
}

export async function pushLayoutToServer(layout) {
  try {
    await fetch('/api/agent-portal/state/layout', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ layout }),
    })
  } catch {
    // Cache + retry-on-next-change semantics; no UI surface for one-off
    // failures to keep the write path noise-free.
  }
}

// ---------- Launch history -------------------------------------------------

export function loadLaunchHistoryFromCache() {
  try {
    const raw = localStorage.getItem(LAUNCH_HISTORY_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((e) => e && typeof e.command === 'string')
  } catch {
    return []
  }
}

export async function fetchLaunchHistoryFromServer() {
  try {
    const res = await fetch('/api/agent-portal/state/launch-history', { credentials: 'include' })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.entries) ? body.entries : null
  } catch {
    return null
  }
}

export async function uploadLaunchHistoryToServer(entries) {
  try {
    const res = await fetch('/api/agent-portal/state/launch-history', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entries }),
    })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.entries) ? body.entries : null
  } catch {
    return null
  }
}

export async function upsertLaunchHistoryEntry(entry) {
  try {
    const res = await fetch('/api/agent-portal/state/launch-history', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ entry }),
    })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.entries) ? body.entries : null
  } catch {
    return null
  }
}

export async function deleteLaunchHistoryEntry(dedupKey) {
  try {
    const res = await fetch('/api/agent-portal/state/launch-history/delete', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dedup_key: dedupKey }),
    })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.entries) ? body.entries : null
  } catch {
    return null
  }
}

// Mirror of the server's _make_dedup_key (sha256 over command|args|cwd|sandboxMode
// joined by U+001F). The frontend needs it so it can pass dedup keys to
// the delete endpoint without an extra round trip — the server returns
// raw entries, not keys, in the GET payload.
export async function computeDedupKey(entry) {
  const parts = [
    String(entry.command ?? ''),
    String(entry.argsString ?? ''),
    String(entry.cwd ?? ''),
    String(entry.sandboxMode ?? entry.sandbox_mode ?? 'off'),
  ]
  const raw = parts.join('')
  if (window.crypto?.subtle) {
    const buf = new TextEncoder().encode(raw)
    const digest = await window.crypto.subtle.digest('SHA-256', buf)
    const bytes = new Uint8Array(digest)
    let hex = ''
    for (let i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, '0')
    }
    return hex
  }
  // No subtle crypto (very old browsers, file://) — fall back to a
  // non-cryptographic key. The server's check is per-user-scoped so
  // collision risk is bounded to the user's own list, not catastrophic.
  let h = 0
  for (let i = 0; i < raw.length; i++) {
    h = ((h << 5) - h + raw.charCodeAt(i)) | 0
  }
  return `js_${(h >>> 0).toString(16)}`
}

// ---------- Launch configs (legacy bag, distinct from server presets) ------

export function loadLaunchConfigsFromCache() {
  try {
    const raw = localStorage.getItem(LAUNCH_CONFIGS_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((e) => e && typeof e.name === 'string')
  } catch {
    return []
  }
}

export async function fetchLaunchConfigsFromServer() {
  try {
    const res = await fetch('/api/agent-portal/state/launch-configs', { credentials: 'include' })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.configs) ? body.configs : null
  } catch {
    return null
  }
}

export async function uploadLaunchConfigsToServer(configs) {
  try {
    const res = await fetch('/api/agent-portal/state/launch-configs', {
      method: 'PUT',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ configs }),
    })
    const body = await jsonOrNull(res)
    return Array.isArray(body?.configs) ? body.configs : null
  } catch {
    return null
  }
}

// Cache keys exported for consumers that want to wipe / migrate.
export const CACHE_KEYS = {
  LAUNCH_HISTORY: LAUNCH_HISTORY_KEY,
  LAUNCH_CONFIGS: LAUNCH_CONFIGS_KEY,
  LAYOUT: LAYOUT_KEY,
}
