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
// This module is pure: it takes the catalogue and the current selection and
// answers "is the search tool on, and is it on because of the sources?".

import { ATLAS_SERVER } from '../constants/atlasTools'

// The search tool's short name inside the built-in server, and the key the
// tool panels use for it ("<server>_<tool>"). Another MCP server may have a
// tool called "search" too; only this one is the built-in.
export const ATLAS_SEARCH_TOOL = 'search'
export const SEARCH_TOOL_KEY = `${ATLAS_SERVER}_${ATLAS_SEARCH_TOOL}`

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

// Whether the turn would carry data sources.
//
// Hand-picked sources always count. The RAG toggle counts too: it means
// "search my data", and the client then sends every source the user can
// reach -- which is exactly what the server would read.
export const sourcesInPlay = ({ selectedDataSources, ragEnabled, reachableSourceCount }) => {
  if (selectedDataSources && selectedDataSources.size > 0) return true
  return Boolean(ragEnabled) && (reachableSourceCount || 0) > 0
}

// Whether the search tool is on because of the user's data sources.
//
// The server adds it to the turn's tool list under the same condition, so
// the panels and the model always agree.
export const searchToolAutoEnabled = ({
  tools,
  selectedDataSources,
  ragEnabled,
  reachableSourceCount,
}) => searchToolOffered(tools)
  && sourcesInPlay({ selectedDataSources, ragEnabled, reachableSourceCount })

// The note the panels show next to an automatically enabled search tool.
export const SEARCH_AUTO_ENABLED_NOTE = 'Automatically enabled by selected data sources.'
