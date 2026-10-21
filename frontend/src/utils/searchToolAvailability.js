// The built-in search tool and the state it is in (issue #1102).
//
// Picking a data source and ticking the built-in search tool used to be two
// separate mechanisms, and a user who did only the first got a turn that read
// nothing. A selection of data sources now implies the search tool: the model
// is offered it and decides whether to call it.
//
// The tool panels mirror that state, so the user can see why their sources
// are read (or not) and cannot switch the tool off while the sources are
// selected -- turning it off would contradict the selection they just made.
//
// This module is pure: it takes the catalogue, the sources the turn would
// carry and the selected model, and answers "is the search tool on, and is it
// on because of the sources?".

import { ATLAS_SERVER, migrateToolName } from '../constants/atlasTools'

// The search tool's short name inside the built-in server, and the key the
// tool panels use for it ("<server>_<tool>"). Another MCP server may have a
// tool called "search" too; only this one is the built-in.
export const ATLAS_SEARCH_TOOL = 'search'
export const SEARCH_TOOL_KEY = `${ATLAS_SERVER}_${ATLAS_SEARCH_TOOL}`

// The keys of the built-in tools.
//
// The server publishes its own list of built-in tool names (a built-in that
// the deployment has gated off is still a built-in), so the client uses that
// rather than deriving it from the catalogue it happens to have.
export const builtinToolKeys = (tools, builtinNames) => {
  if (Array.isArray(builtinNames) && builtinNames.length > 0) {
    return new Set(builtinNames)
  }
  const server = (tools || []).find(s => s?.server === ATLAS_SERVER)
  return new Set((server?.tools || []).map(t => `${ATLAS_SERVER}_${t}`))
}

// Whether the tool is outbound.
//
// A tool is outbound unless it belongs to the built-in server: an operator
// may configure a server whose name starts with "atlas_", and that server
// is outbound.
export const hasOutboundTool = (selectedTools, tools, builtinNames) => {
  const builtins = builtinToolKeys(tools, builtinNames)
  return [...(selectedTools || [])].some(k => !builtins.has(migrateToolName(k)))
}

// Whether the user selected the search tool in their own right.
//
// Compared through the migration, so a selection that still carries the
// pre-#855 name counts (and is not counted twice).
export const isSearchSelected = (selectedTools) =>
  [...(selectedTools || [])].some(k => migrateToolName(k) === SEARCH_TOOL_KEY)

// Whether the deployment offers the search tool at all.
//
// The backend drops it from the catalogue when the RAG tools feature is off
// (``FEATURE_ATLAS_RAG_TOOLS_ENABLED``), so the catalogue is the authority --
// no extra feature flag has to be kept in step with it.
export const searchToolOffered = (tools) =>
  (tools || []).some(
    server => server?.server === ATLAS_SERVER
      && (server.tools || []).includes(ATLAS_SEARCH_TOOL)
  )

// Whether the selected model can call tools at all.
//
// The backend strips every tool for a model that cannot, so the search tool
// is not on the table for such a turn -- and saying it is would be a lie the
// user can check by sending a message.
export const modelSupportsTools = (models, currentModel) => {
  const entry = (models || []).find(m => (m?.name || m) === currentModel)
  if (!entry || typeof entry !== 'object') return true
  return entry.supports_tools !== false
}

// Whether the search tool is on because of the user's data sources.
//
// The server adds it to the turn's tool list under the same condition, so
// the panels and the model always agree. `effectiveDataSources` is the list
// the turn would carry -- the selected sources, or every reachable one when
// the RAG toggle is on, minus the ones the active compliance level excludes.
// `searchWithOutboundTools` mirrors the deployment's setting of the same
// name: when it is off, the server keeps the implied tool out of a turn that
// carries an MCP server's tool, and so must the panel.
export const searchToolAutoEnabled = ({
  tools,
  effectiveDataSources,
  currentModel,
  models,
  selectedTools,
  searchWithOutboundTools,
  builtinNames
}) => searchToolOffered(tools)
  && modelSupportsTools(models, currentModel)
  && (effectiveDataSources || []).length > 0
  && (searchWithOutboundTools !== false
    || !hasOutboundTool(selectedTools, tools, builtinNames))

// The note the panels show next to an automatically enabled search tool.
export const SEARCH_AUTO_ENABLED_NOTE = 'Automatically enabled by selected data sources.'

// The extra line for the case where the tool is *also* selected in its own
// right: the sources imply it, but the user's own tick is what keeps it on
// once they deselect the sources.
export const SEARCH_EXPLICIT_NOTE = 'Also selected on its own, so it stays on when the sources are deselected.'

// The tooltip for that case: clicking removes the user's own tick, and the
// tool stays on because the sources imply it.
export const SEARCH_EXPLICIT_REMOVE_TITLE = 'Remove your selection. The tool stays on while the data sources are selected.'

// The tooltip for the case where the tool is only implied: clicking selects
// it in its own right, so the user can express that choice (and undo it
// later).
export const SEARCH_AUTO_ENABLED_SELECT_TITLE = 'Automatically enabled by selected data sources. Click to select it explicitly.'
