/**
 * The one compliance-access rule every surface applies.
 *
 * The header compliance selector filters tools, prompts, personas, data
 * sources and models. Each surface used to carry its own copy of the rule and
 * they drifted apart: the pickers honored the allowlist (HIPAA may use SOC2)
 * while the cleanup that runs on a level switch compared names for equality,
 * so a switch to HIPAA deselected SOC2 tools the panel still showed, and kept
 * untagged tools the panel hid. Keep the rule here so they cannot drift again.
 *
 * Rule (explicit allowlist, strict):
 *   - no level selected            -> everything is accessible
 *   - level selected, resource has none -> denied (an untagged resource has
 *     no declared boundary, so it cannot be trusted under a filter)
 *   - unknown selected level       -> denied
 *   - otherwise the resource level (aliases resolved) must be in the selected
 *     level's `allowed_with` list.
 */

const canonicalName = (levels, name) => {
  if (!name) return name
  for (const level of levels) {
    if (level.name === name) return level.name
    if (Array.isArray(level.aliases) && level.aliases.includes(name)) return level.name
  }
  return name
}

export const isComplianceAccessible = (levels, userLevel, resourceLevel) => {
  if (!userLevel) return true
  if (!resourceLevel) return false
  const list = Array.isArray(levels) ? levels : []
  const userName = canonicalName(list, userLevel)
  const userLevelObj = list.find(l => l.name === userName)
  if (!userLevelObj || !Array.isArray(userLevelObj.allowed_with)) return false
  return userLevelObj.allowed_with.includes(canonicalName(list, resourceLevel))
}

/**
 * Whether the level definitions needed to apply a filter are present.
 *
 * Until /api/compliance-levels answers, every check would deny (unknown
 * selected level), so callers that *remove* selections must wait for this --
 * otherwise a page load with a persisted filter would wipe every selection.
 */
export const complianceLevelsReady = (levels, userLevel) =>
  !userLevel || (Array.isArray(levels) && levels.some(l => l.name === canonicalName(levels, userLevel)))

/**
 * Level marker for resources no compliance level filters out: the built-in
 * `atlas` server runs in-process (canvas, sleep, search over sources that
 * are compliance-checked on their own), so hiding it under a non-Public
 * level would only take away `atlas_canvas`, which is always available.
 */
export const COMPLIANCE_EXEMPT = 'compliance-exempt'

/**
 * The subset of `keys` the active filter excludes.
 *
 * `levelOf(key)` returns the resource's level (null when untagged),
 * COMPLIANCE_EXEMPT, or `undefined` when it cannot place the key at all --
 * an unknown key (config not loaded yet, server gone) is left alone here
 * rather than guessed at; the send path drops such keys separately.
 * Returns nothing until the level definitions are loaded.
 */
export const keysExcludedByCompliance = (keys, levels, userLevel, levelOf) => {
  if (!userLevel || !complianceLevelsReady(levels, userLevel)) return []
  return [...keys].filter(key => {
    const level = levelOf(key)
    return level !== undefined && level !== COMPLIANCE_EXEMPT &&
      !isComplianceAccessible(levels, userLevel, level)
  })
}

const modelEntry = m => (typeof m === 'string' ? { name: m } : (m || {}))

/** Whether the model named `modelName` is allowed under `userLevel`. */
export const isModelComplianceAccessible = (models, levels, userLevel, modelName) => {
  if (!userLevel || !modelName || !complianceLevelsReady(levels, userLevel)) return true
  const entry = (models || []).map(modelEntry).find(m => m.name === modelName)
  // A model the list does not know (stale persisted choice) cannot be placed.
  if (!entry) return true
  return isComplianceAccessible(levels, userLevel, entry.compliance_level)
}

/**
 * Model to switch to under `userLevel`, or null: the first model at exactly
 * that level, else the first one the level allows. Skips models that need a
 * per-user API key the user has not supplied, since they cannot be selected.
 */
export const firstCompliantModel = (models, levels, userLevel) => {
  const usable = (models || []).map(modelEntry).filter(m =>
    m.name &&
    isComplianceAccessible(levels, userLevel, m.compliance_level) &&
    !(m.api_key_source === 'user' && m.user_has_key !== true)
  )
  const exact = usable.find(m => canonicalName(levels, m.compliance_level) === canonicalName(levels, userLevel))
  const entry = exact || usable[0]
  return entry ? entry.name : null
}
