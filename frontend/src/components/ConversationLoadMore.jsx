/**
 * The control at the bottom of the saved-conversations list that walks the
 * user back through older conversations (issue #1085).
 *
 * It shows one of three things: a button that fetches the next page, the
 * progress of that fetch, or -- when the fetch failed -- the reason and a
 * retry. Once every conversation has been loaded the control says so and
 * stops offering a page that does not exist.
 */
const ConversationLoadMore = ({ hasMore, loading, error, onLoadMore }) => {
  if (error) {
    return (
      <div className="px-3 py-2 border-t border-gray-700 flex flex-col items-center gap-1">
        <p className="text-xs text-red-400" role="alert">{error}</p>
        <button
          onClick={onLoadMore}
          disabled={loading}
          className="w-full text-xs px-2 py-1.5 text-blue-400 hover:bg-blue-900/30 rounded transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {loading ? 'Retrying...' : 'Retry'}
        </button>
      </div>
    )
  }

  if (hasMore) {
    return (
      <div className="px-3 py-2 border-t border-gray-700 flex flex-col items-center gap-1">
        <button
          onClick={onLoadMore}
          disabled={loading}
          className="w-full text-xs px-2 py-1.5 text-blue-400 hover:bg-blue-900/30 rounded transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {loading ? 'Loading older conversations...' : 'Load more'}
        </button>
        {loading && (
          <span className="text-xs text-gray-500" role="status">Fetching the next page...</span>
        )}
      </div>
    )
  }

  return (
    <div className="px-3 py-2 border-t border-gray-700 text-center text-xs text-gray-500">
      All conversations loaded
    </div>
  )
}

export default ConversationLoadMore
