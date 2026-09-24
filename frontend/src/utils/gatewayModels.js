/**
 * Team-scoped models reached through an enterprise LiteLLM gateway.
 *
 * The user picks a LiteLLM team, then one of that team's models. The pick is
 * an ordinary model name of the form `<gateway>::<team_id>::<model_id>`, so
 * the chat request, saved conversations and everything else keyed on the
 * model name carry the team with it. Mirrors
 * atlas/modules/config/litellm_gateway_models.py.
 */

export const GATEWAY_KEY_SEPARATOR = '::'

const TEAM_LABELS_STORAGE_KEY = 'chatui-gateway-team-labels'
const LAST_TEAM_STORAGE_KEY = 'chatui-gateway-last-team'

/** Split a gateway model key; null for any other name or an unknown gateway. */
export function parseGatewayModelKey(name, gateways) {
  if (typeof name !== 'string') return null
  const first = name.indexOf(GATEWAY_KEY_SEPARATOR)
  if (first <= 0) return null
  const second = name.indexOf(GATEWAY_KEY_SEPARATOR, first + GATEWAY_KEY_SEPARATOR.length)
  if (second < 0) return null
  const gateway = name.slice(0, first)
  const teamId = name.slice(first + GATEWAY_KEY_SEPARATOR.length, second)
  const modelId = name.slice(second + GATEWAY_KEY_SEPARATOR.length)
  if (!teamId.trim() || !modelId.trim()) return null
  if (gateways && !gateways.some(g => g.name === gateway)) return null
  return { gateway, teamId, modelId }
}

function readJson(key) {
  try {
    const raw = localStorage.getItem(key)
    const parsed = raw ? JSON.parse(raw) : {}
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Storage is a convenience here; the picker works without it.
  }
}

/** Remember a team's display label so the chosen model can be shown by name. */
export function rememberTeamLabel(gateway, teamId, label) {
  const labels = readJson(TEAM_LABELS_STORAGE_KEY)
  labels[`${gateway}${GATEWAY_KEY_SEPARATOR}${teamId}`] = label
  writeJson(TEAM_LABELS_STORAGE_KEY, labels)
}

export function teamLabelFor(gateway, teamId) {
  return readJson(TEAM_LABELS_STORAGE_KEY)[`${gateway}${GATEWAY_KEY_SEPARATOR}${teamId}`] || teamId
}

export function rememberLastTeam(gateway, teamId) {
  const last = readJson(LAST_TEAM_STORAGE_KEY)
  last[gateway] = teamId
  writeJson(LAST_TEAM_STORAGE_KEY, last)
}

export function lastTeamFor(gateway) {
  return readJson(LAST_TEAM_STORAGE_KEY)[gateway] || ''
}

/**
 * The model-list entry for a gateway model key, shaped like the entries in
 * /api/config `models` (capabilities come from the gateway's model defaults).
 */
export function gatewayModelEntry(name, gateways) {
  const ref = parseGatewayModelKey(name, gateways)
  if (!ref) return null
  const gateway = gateways.find(g => g.name === ref.gateway)
  const teamLabel = teamLabelFor(ref.gateway, ref.teamId)
  return {
    name,
    gateway: ref.gateway,
    team_id: ref.teamId,
    team_label: teamLabel,
    model_id: ref.modelId,
    display_name: `${ref.modelId} (${teamLabel})`,
    description: gateway.description || `${ref.modelId} via ${gateway.display_name || gateway.name}`,
    compliance_level: gateway.compliance_level,
    supports_vision: !!gateway.supports_vision,
    supports_pdf: !!gateway.supports_pdf,
    supports_tools: gateway.supports_tools !== false,
  }
}

/** The configured models plus an entry for the current gateway model, if any. */
export function withGatewayModel(models, currentModel, gateways) {
  const entry = gatewayModelEntry(currentModel, gateways || [])
  if (!entry) return models
  return [...models.filter(m => (m?.name || m) !== currentModel), entry]
}
