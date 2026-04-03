import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const HelpPage = () => {
  const navigate = useNavigate()
  const [helpContent, setHelpContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    const fetchHelpContent = async () => {
      try {
        const response = await fetch('/api/config')
        if (!response.ok) {
          throw new Error('Failed to fetch help content')
        }
        const data = await response.json()
        setHelpContent(data.help_content || '')
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

  return (
    <div className="h-screen bg-gray-900 text-gray-200 flex flex-col">
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

          {!loading && !error && (
            <div
              className="prose prose-invert max-w-none"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(helpContent) }}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default HelpPage
