// The data sources a chat turn carries.
//
// RAG is activated when the RAG toggle is on, when the user picked sources,
// or when the built-in search tool is selected for the turn: ticking it with
// nothing picked means "everything I can reach" -- an explicitly empty
// source list would mean "query nothing" (issue #855).
//
// `searchToolSelected` is deliberately *not* the same in every caller:
//
//   - the wire payload wants it. Ticking the search tool on its own means
//     "search everything I can reach", so the turn has to carry the sources.
//   - the tool panels must NOT have it. The search tool's own selection
//     would then expand the source list, which would lock the tool on --
//     and a user who ticked it could never untick it (issue #1102).
//
// Both callers use this one helper, so the rule lives in one place and the
// difference is explicit rather than accidental.

export const resolveTurnDataSources = ({
	selectedDataSources,
	ragEnabled = false,
	searchToolSelected = false,
	allSourceIds = [],
	dropExcluded = keys => keys,
}) => {
	const hasSelectedSources = Boolean(selectedDataSources && selectedDataSources.size > 0)
	const ragActivated = ragEnabled || hasSelectedSources || searchToolSelected
	if (!ragActivated) return []
	const sources = hasSelectedSources ? [...selectedDataSources] : allSourceIds
	return dropExcluded(sources)
}

// Why the built-in search tool is on, when it is on because of the sources:
// 'sources' when the user picked them, 'rag' when the RAG toggle expanded
// them, and null when no source is in play.
export const searchToolReason = ({ selectedDataSources, effectiveDataSources }) => {
	if (!effectiveDataSources || effectiveDataSources.length === 0) return null
	return selectedDataSources && selectedDataSources.size > 0 ? 'sources' : 'rag'
}
