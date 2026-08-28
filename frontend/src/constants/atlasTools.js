// The built-in ATLAS tools (issue #855).
//
// Canvas, sleep, search and source discovery used to be spread across three
// separate pseudo-servers (`canvas`, `atlas_agent`, `atlas_rag`). They are one
// server now -- `atlas` -- so the tools panel shows one built-in entry instead
// of three, pinned to the top of every server list.
//
// Tool selections live in localStorage, so browsers still hold the old names.
// `migrateToolName` maps them forward; run it wherever a persisted or
// server-sent tool name is read.

export const ATLAS_SERVER = 'atlas'

export const CANVAS_TOOL = 'atlas_canvas'
export const SLEEP_TOOL = 'atlas_sleep'
export const SEARCH_TOOL = 'atlas_search'
export const DISCOVER_TOOL = 'atlas_discover_sources'

const LEGACY_TOOL_ALIASES = {
  canvas_canvas: CANVAS_TOOL,
  atlas_agent_sleep: SLEEP_TOOL,
  atlas_rag_query: SEARCH_TOOL,
  atlas_rag_discover_data_sources: DISCOVER_TOOL,
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


// The built-in server is pinned to the top of the tools and marketplace lists
// so it is always in the same place, however many MCP servers a user has
// selected. Everything else keeps the order the backend sent, which the panels
// already relied on -- this only lifts `atlas` out of it.
export const sortAtlasFirst = servers => {
  if (!Array.isArray(servers)) return servers
  const atlas = []
  const rest = []
  for (const entry of servers) {
    if (entry?.server === ATLAS_SERVER) atlas.push(entry)
    else rest.push(entry)
  }
  return [...atlas, ...rest]
}
