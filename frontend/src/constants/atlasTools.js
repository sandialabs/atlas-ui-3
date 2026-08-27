// The built-in ATLAS tools (issue #855).
//
// Canvas, sleep and search used to be three separate pseudo-servers
// (`canvas`, `atlas_agent`, `atlas_rag`). They are one server now -- `atlas` --
// so the tools panel shows one built-in entry instead of three.
//
// Tool selections live in localStorage, so browsers still hold the old names.
// `migrateToolName` maps them forward; run it wherever a persisted or
// server-sent tool name is read.

export const ATLAS_SERVER = 'atlas'

export const CANVAS_TOOL = 'atlas_canvas'
export const SLEEP_TOOL = 'atlas_sleep'
export const SEARCH_TOOL = 'atlas_search'

const LEGACY_TOOL_ALIASES = {
  canvas_canvas: CANVAS_TOOL,
  atlas_agent_sleep: SLEEP_TOOL,
  atlas_rag_query: SEARCH_TOOL,
  // Search now uses the selected data sources, so there is nothing left for a
  // separate discover step to feed; a stored selection of it maps to search.
  atlas_rag_discover_data_sources: SEARCH_TOOL,
}

export const migrateToolName = name =>
  (typeof name === 'string' && LEGACY_TOOL_ALIASES[name]) || name

export const migrateToolNames = names => {
  if (!Array.isArray(names)) return names
  const seen = new Set()
  const out = []
  for (const name of names) {
    const migrated = migrateToolName(name)
    if (seen.has(migrated)) continue
    seen.add(migrated)
    out.push(migrated)
  }
  return out
}

export const isCanvasTool = name => migrateToolName(name) === CANVAS_TOOL
