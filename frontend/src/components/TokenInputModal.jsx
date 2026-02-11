import { useState, useEffect, useRef } from 'react'
import { X, Upload, RefreshCw, Eye, EyeOff } from 'lucide-react'

/**
 * TokenInputModal
 *
 * A reusable modal for entering API keys or tokens for MCP server authentication.
 * Supports any type of token that can be used in an Authorization header:
 * - API keys
 * - JWT tokens
 * - Bearer tokens
 * - Any other authentication token
 *
 * Props:
 * - isOpen: boolean - Whether the modal is visible
 * - serverName: string - Name of the server to authenticate
 * - onClose: function - Called when modal should close
 * - onUpload: function(tokenData) - Called with { token, expires_at } when user submits
 * - isLoading: boolean - Whether upload is in progress
 * - error: string - Error message to display (optional)
 *
 * Updated: 2026-01-25
 */
const TokenInputModal = ({ isOpen, serverName, onClose, onUpload, isLoading, error }) => {
  const [tokenInput, setTokenInput] = useState('')
  const [tokenExpiry, setTokenExpiry] = useState('')
  const [showToken, setShowToken] = useState(false)
  const inputRef = useRef(null)
  const submitRef = useRef(null)

  // Reset form and focus input when modal opens
  useEffect(() => {
    if (isOpen) {
      // Focus the input after a short delay to ensure modal is rendered
      setTimeout(() => {
        inputRef.current?.focus()
      }, 50)
    } else {
      setTokenInput('')
      setTokenExpiry('')
      setShowToken(false)
    }
  }, [isOpen])

  const handleSubmit = () => {
    if (!tokenInput.trim() || isLoading) return

    const tokenData = {
      token: tokenInput.trim(),
    }

    // Parse expiry date if provided
    if (tokenExpiry) {
      const expiryDate = new Date(tokenExpiry)
      if (!isNaN(expiryDate.getTime())) {
        tokenData.expires_at = expiryDate.getTime() / 1000  // Convert to Unix timestamp
      }
    }

    onUpload(tokenData)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    } else if (e.key === 'Tab' && !e.shiftKey && e.target === inputRef.current) {
      // Tab from input goes directly to Upload button, skipping expiration
      e.preventDefault()
      submitRef.current?.focus()
    }
  }

  const handleClose = () => {
    setTokenInput('')
    setTokenExpiry('')
    setShowToken(false)
    onClose()
  }

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 bg-black bg-opacity-70 flex items-center justify-center z-[100]"
      onClick={handleClose}
    >
      <div
        className="bg-gray-800 rounded-lg shadow-xl max-w-lg w-full mx-4 p-6"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={handleKeyDown}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold text-gray-100 flex items-center gap-2">
            <Upload className="w-5 h-5" />
            Upload Token for {serverName}
          </h3>
          <button
            onClick={handleClose}
            className="p-1 rounded hover:bg-gray-700 transition-colors"
          >
            <X className="w-5 h-5 text-gray-400" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              API Key or Token
            </label>
            <div className="relative">
              <input
                ref={inputRef}
                type={showToken ? "text" : "password"}
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                placeholder="Paste your API key, JWT, or bearer token here..."
                className="w-full px-3 py-2 pr-10 bg-gray-700 text-gray-100 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500 font-mono text-sm"
                autoComplete="off"
                spellCheck="false"
                autoFocus
              />
              <button
                type="button"
                onClick={() => setShowToken(!showToken)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-200 transition-colors"
                tabIndex={-1}
              >
                {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
            <p className="text-xs text-gray-400 mt-1">
              Press Enter to submit
            </p>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-300 mb-2">
              Expiration (optional)
            </label>
            <input
              type="datetime-local"
              value={tokenExpiry}
              onChange={(e) => setTokenExpiry(e.target.value)}
              className="w-full px-3 py-2 bg-gray-700 text-gray-100 rounded-lg border border-gray-600 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-400 mt-1">
              Set when the token expires. Leave empty if the token doesn't expire.
            </p>
          </div>

          {error && (
            <div className="p-3 bg-red-900/30 border border-red-700 rounded-lg text-red-300 text-sm">
              {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button
            onClick={handleClose}
            className="px-4 py-2 rounded-lg bg-gray-600 hover:bg-gray-500 text-gray-200 transition-colors"
          >
            Cancel
          </button>
          <button
            ref={submitRef}
            onClick={handleSubmit}
            disabled={!tokenInput.trim() || isLoading}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors disabled:opacity-50"
          >
            {isLoading ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            Upload Token
          </button>
        </div>
      </div>
    </div>
  )
}

export default TokenInputModal
