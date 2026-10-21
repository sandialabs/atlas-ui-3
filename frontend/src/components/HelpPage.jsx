import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ExternalLink } from 'lucide-react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

// The Help page renders whatever the server says the Help source is
// (issue #1107):
//
//   local            the Markdown that ships with ATLAS, or the operator's
//                    override -- unchanged from before
//   remote_markdown  a document fetched server-side from the operator's
//                    documentation host, with its images resolved there
//   external_link    nothing to render: the documentation lives on another
//                    site, and the Help button opens it in a new tab
//
// The server also reports a configuration error when the deployment asked
// for an external source it cannot use; that is shown here rather than
// swallowed, because the fallback (the local document) is not what the
// operator configured.
const HelpPage = () => {
  const navigate = useNavigate()
  const [helpData, setHelpData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const fetchHelpContent = async () => {
      try {
        const response = await fetch('/api/help')
        if (!response.ok) {
          throw new Error('Failed to fetch help content')
        }
        const data = await response.json()
        setHelpData(data)
      } catch (err) {
        setError(err.message)
      } finally {
        setLoading(false)
      }
    }

    fetchHelpContent()
  }, [])

  const renderMarkdown = (content) => {
    try {
      const html = marked.parse(content)
      return DOMPurify.sanitize(html)
    } catch (err) {
      console.error('Error rendering help content:', err)
      return ''
    }
  }

  const mode = helpData?.mode || 'local'
  const linkUrl = helpData?.link_url || ''
  // A configuration problem and a documentation host that is down are
  // different things. The first needs an operator to change the
  // configuration; the second usually resolves itself, and the copy on
  // screen is still worth reading.
  const configError = helpData?.error || null
  const fetchError = helpData?.fetch_error || null
  const outageNotice = fetchError
    ? helpData?.stale
      ? 'The hosted documentation could not be refreshed. This is the last copy that was fetched.'
      : helpData?.fallback === 'local'
        ? 'The hosted documentation could not be reached. The local Help content is shown instead.'
        : fetchError
    : null

  return (
    <div className="h-full bg-gray-900 text-gray-200 flex flex-col">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700 p-4 flex-shrink-0">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/')}
            className="p-2 rounded-lg bg-gray-700 hover:bg-gray-600 transition-colors"
            title="Back to Chat"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <h1 className="text-2xl font-bold">Help</h1>
        </div>
      </header>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        <div className="w-full max-w-4xl mx-auto px-6 py-6">
          {loading && (
            <div className="bg-gray-800 rounded-lg p-6 text-center text-gray-400">
              <div className="animate-pulse">Loading help content...</div>
            </div>
          )}

          {error && (
            <div className="bg-red-900/20 border border-red-700 rounded-lg p-6 text-center text-red-400">
              <div className="flex items-center justify-center gap-2">
                <div className="w-5 h-5 bg-red-500 rounded-full flex items-center justify-center text-white text-xs">!</div>
                Error loading help content: {error}
              </div>
            </div>
          )}

          {/* A misconfiguration is worth saying out loud: the operator asked
              for hosted documentation and ATLAS is showing something else. */}
          {!loading && !error && configError && (
            <div className="bg-yellow-900/20 border border-yellow-700 rounded-lg p-4 mb-6 text-yellow-300">
              <div className="font-medium mb-1">Help configuration problem</div>
              <div className="text-sm">{configError}</div>
            </div>
          )}

          {/* A documentation host that is down is not a configuration
              problem: the content on screen is still the right content,
              just not the newest copy. */}
          {!loading && !error && outageNotice && (
            <div className="bg-gray-800 border border-gray-700 rounded-lg p-4 mb-6 text-gray-300">
              <div className="font-medium mb-1 text-gray-200">
                The hosted documentation is not available right now
              </div>
              <div className="text-sm">{outageNotice}</div>
            </div>
          )}

          {!loading && !error && mode === 'external_link' && (
            <div className="bg-gray-800 rounded-lg p-6">
              <h2 className="text-lg font-semibold mb-2">Documentation</h2>
              <p className="text-gray-400 mb-4">
                The documentation for this deployment is hosted on an external site.
              </p>
              {linkUrl && (
                <a
                  href={linkUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 transition-colors"
                >
                  <ExternalLink className="w-4 h-4" />
                  Open the documentation
                </a>
              )}
            </div>
          )}

          {!loading && !error && mode !== 'external_link' && (
            <div
              className="prose prose-invert max-w-none"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(helpData?.content || '') }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default HelpPage
