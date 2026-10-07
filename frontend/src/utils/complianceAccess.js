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
 * Rule (explicit data classifications, issue #1032):
 *   - no level selected            -> everything is accessible
 *   - level selected, resource declares nothing -> denied (an undeclared
 *     resource is approved for no classified session)
 *   - unknown selected level       -> denied
 *   - otherwise the selected level (aliases resolved) must be one of the
 *     resource's `allowed_data_classifications`. A legacy single
 *     `compliance_level` counts as a one-element list. `allowed_with` in the
 *     level definitions no longer widens access: a level never makes another
 *     level's resources valid by implication.
 *
 * The server applies the same rule to every chat turn, so this filtering is a
 * convenience, not the boundary.
 */

const canonicalName = (levels, name) => {
  if (!name) return name
  for (const level of levels) {
    if (level.name === name) return level.name
    if (Array.isArray(level.aliases) && level.aliases.includes(name)) return level.name
  }
  return name
}

/**
 * The classifications a resource (model, MCP server or prompt entry, RAG
 * server or source) is approved for: its `allowed_data_classifications`
 * (snake or camel case) when present, else its legacy level as a one-element
 * list, else null (undeclared).
 */
export const classificationsOf = resource => {
  if (!resource || typeof resource !== 'object') return null
  const allowed = resource.allowed_data_classifications ?? resource.allowedDataClassifications
  if (Array.isArray(allowed)) return allowed
  const legacy = resource.compliance_level ?? resource.complianceLevel
  return legacy ? [legacy] : null
}

/** Badge text for a resource's classifications ("UUR, ITAR"), or null. */
export const classificationLabel = resource => {
  const list = classificationsOf(resource)
  return list && list.length ? list.join(', ') : null
}

/**
 * `resourceClassifications` is a list of classifications, a single legacy
 * level string, or null/undefined when the resource declares nothing.
 */
export const isComplianceAccessible = (levels, userLevel, resourceClassifications) => {
  if (!userLevel) return true
  const declared = typeof resourceClassifications === 'string'
    ? [resourceClassifications]
    : resourceClassifications
  if (!Array.isArray(declared) || declared.length === 0) return false
  const list = Array.isArray(levels) ? levels : []
  const userName = canonicalName(list, userLevel)
  if (!list.some(l => l.name === userName)) return false
  return declared.some(name => canonicalName(list, name) === userName)
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
 * `levelOf(key)` returns the resource's classifications (null when undeclared),
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
  return isComplianceAccessible(levels, userLevel, classificationsOf(entry))
}

/**
 * Model to switch to under `userLevel`, or null: the first model approved for
 * that classification. Skips models that need a per-user API key the user has
 * not supplied, since they cannot be selected.
 */
export const firstCompliantModel = (models, levels, userLevel) => {
  const entry = (models || []).map(modelEntry).find(m =>
    m.name &&
    isComplianceAccessible(levels, userLevel, classificationsOf(m)) &&
    !(m.api_key_source === 'user' && m.user_has_key !== true)
  )
  return entry ? entry.name : null
}
