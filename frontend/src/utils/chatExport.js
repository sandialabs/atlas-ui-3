// Helpers for the chat-download / export flow.
//
// The frontend does not have the actual prompt body (that lives on the backend
// MCP server), so when the user selects a custom prompt mid-conversation we
// surface the prompt's name / description / server in the export instead.

export function buildPromptInfoByKey(promptsConfig) {
  const out = {}
  ;(promptsConfig || []).forEach(server => {
    (server.prompts || []).forEach(p => {
      const key = `${server.server}_${p.name}`
      out[key] = {
        key,
        name: p.name,
        description: p.description || '',
        server: server.server,
      }
    })
  })
  return out
}

export function resolvePromptInfo(key, promptInfoByKey) {
  if (!key) return null
  return promptInfoByKey[key] || { key, name: key, description: '', server: '' }
}

// Walk through messages and inject synthetic system entries at points where the
// active custom prompt changed (based on the per-user-message _activePromptKey
// snapshot). Strips _activePromptKey from the exported messages.
export function buildExportConversation(messages, promptInfoByKey) {
  const out = []
  let prev = null
  let sawAny = false
  for (const m of messages) {
    if (m && m.role === 'user' && Object.prototype.hasOwnProperty.call(m, '_activePromptKey')) {
      const cur = m._activePromptKey || null
      if (!sawAny || cur !== prev) {
        if (cur) {
          const info = resolvePromptInfo(cur, promptInfoByKey)
          out.push({
            role: 'system',
            content: info.description
              ? `Custom prompt activated: ${info.name} (from ${info.server}) — ${info.description}`
              : `Custom prompt activated: ${info.name} (from ${info.server})`,
            timestamp: m.timestamp,
            _promptChange: true,
            promptKey: info.key,
            promptName: info.name,
            promptServer: info.server,
            promptDescription: info.description,
          })
        } else if (sawAny && prev) {
          out.push({
            role: 'system',
            content: 'Custom prompt cleared (using default prompt)',
            timestamp: m.timestamp,
            _promptChange: true,
            promptKey: null,
          })
        }
        prev = cur
        sawAny = true
      }
    }
    const { _activePromptKey, ...rest } = m || {}
    out.push(rest)
  }
  return out
}
