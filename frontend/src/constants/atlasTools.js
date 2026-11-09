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

// The subagent tools (issue #1141). They were first named `atlas_launch`,
// `atlas_get_runs`, `atlas_result`, `atlas_follow_up` and
// `atlas_discover_launch_options`; "launch" alone read as starting an ATLAS
// instance rather than delegating a task. The old names are deprecated aliases:
// the backend still runs them, and saved selections and old transcripts still
// carry them, so they migrate forward here like the pre-#855 names do.
export const DISCOVER_SUBAGENT_OPTIONS_TOOL = 'atlas_discover_subagent_options'
export const LAUNCH_SUBAGENT_TOOL = 'atlas_launch_subagent'
export const GET_SUBAGENT_RUNS_TOOL = 'atlas_get_subagent_runs'
export const GET_SUBAGENT_RESULT_TOOL = 'atlas_get_subagent_result'
export const FOLLOW_UP_SUBAGENT_TOOL = 'atlas_follow_up_subagent'
// Kept for callers written before the rename.
export const DISCOVER_LAUNCH_OPTIONS_TOOL = DISCOVER_SUBAGENT_OPTIONS_TOOL

export const DEPRECATED_TOOL_ALIASES = {
  atlas_discover_launch_options: DISCOVER_SUBAGENT_OPTIONS_TOOL,
  atlas_launch: LAUNCH_SUBAGENT_TOOL,
  atlas_get_runs: GET_SUBAGENT_RUNS_TOOL,
  atlas_result: GET_SUBAGENT_RESULT_TOOL,
  atlas_follow_up: FOLLOW_UP_SUBAGENT_TOOL,
}

const LEGACY_TOOL_ALIASES = {
  canvas_canvas: CANVAS_TOOL,
  atlas_agent_sleep: SLEEP_TOOL,
  atlas_rag_query: SEARCH_TOOL,
  atlas_rag_discover_data_sources: DISCOVER_TOOL,
  ...DEPRECATED_TOOL_ALIASES,
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

// How the subagent tools are presented: one "Subagents" group in the tools
// panel, and a title that names the action wherever a call is shown. Display
// only -- the tool name is still what the model calls and a selection stores.
export const SUBAGENT_TOOL_GROUP = 'Subagents'

const SUBAGENT_TOOL_LABELS = {
  [DISCOVER_SUBAGENT_OPTIONS_TOOL]: 'Discover Subagent Options',
  [LAUNCH_SUBAGENT_TOOL]: 'Launch Subagent',
  [GET_SUBAGENT_RUNS_TOOL]: 'Get Subagent Runs',
  [GET_SUBAGENT_RESULT_TOOL]: 'Get Subagent Result',
  [FOLLOW_UP_SUBAGENT_TOOL]: 'Follow Up Subagent',
}

// The display title of a subagent tool, under its current or deprecated name;
// null for every other tool, which is shown by its own name.
export const subagentToolLabel = name => {
  const migrated = migrateToolName(name)
  return (typeof migrated === 'string' && Object.hasOwn(SUBAGENT_TOOL_LABELS, migrated))
    ? SUBAGENT_TOOL_LABELS[migrated]
    : null
}

export const isSubagentTool = name => subagentToolLabel(name) !== null

// The tools-panel group a built-in tool belongs to, or null for none.
export const atlasToolGroup = name => (isSubagentTool(name) ? SUBAGENT_TOOL_GROUP : null)


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
