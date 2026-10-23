// Friendly compliance level labels (issue #1117).
//
// A compliance level's canonical `name` is an identifier: RAG, MCP and LLM
// policies, the APIs and every persisted record compare it. Long canonical
// keys (`CUI_no_ECI_no_ITAR_no_PII`) make poor labels, so a level may
// configure a presentation-only `display_name` in compliance-levels.json.
// The presentation rule everywhere ATLAS shows a level to a user:
//
//     display_name ?? name
//
// and a banner's own `label` still wins over both (issue #1045). This module
// is the one resolver so surfaces cannot drift. It never substitutes the
// label where an identifier is used: filtering, selection values, and the
// payload sent to the server keep the canonical `name`.
//
// `levels` is the definition list from /api/compliance-levels. Callers that
// do not have it yet (definitions still loading, or an admin surface that
// never fetched them) fall back to the raw name, which is exactly how the UI
// behaved before display_name existed.

import { classificationsOf } from './complianceAccess'

/** The label a level definition renders as: its `display_name`, else `name`. */
export const levelDisplayLabel = level => {
  if (!level || typeof level !== 'object') return null
  const label = level.display_name
  if (typeof label === 'string' && label.trim()) return label
  return level.name || null
}

/**
 * The friendly label for a canonical level name or one of its aliases,
 * resolved through `levels`. Falls back to `name` itself when the
 * definitions are missing, still loading, or no longer define the level --
 * an unknown level must keep its recorded name rather than render nothing.
 */
export const complianceDisplayName = (levels, name) => {
  if (!name) return null
  const list = Array.isArray(levels) ? levels : []
  const level = list.find(l => l && (
    l.name === name ||
    (Array.isArray(l.aliases) && l.aliases.includes(name))
  ))
  return (level && levelDisplayLabel(level)) || name
}

/**
 * Badge text for a resource's classifications, each resolved to its friendly
 * label ("CUI (Restricted), ITAR"), or null when it declares none. The
 * resource's declared values stay canonical; only the rendering changes.
 */
export const classificationsDisplayLabel = (resource, levels) => {
  const list = classificationsOf(resource)
  if (!list || list.length === 0) return null
  return list.map(name => complianceDisplayName(levels, name)).join(', ')
}