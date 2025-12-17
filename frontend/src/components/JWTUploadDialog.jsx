import React, { useState } from 'react'
import { Key, Upload, X, CheckCircle, AlertCircle } from 'lucide-react'

const JWTUploadDialog = ({ isOpen, onClose, serverName, onSuccess }) => {
  const [jwtToken, setJwtToken] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)

  if (!isOpen) return null

  const handleUpload = async () => {
    if (!jwtToken.trim()) {
      setError('Please enter a JWT token')
      return
    }

    try {
      setUploading(true)
      setError(null)

      const response = await fetch('/api/user/mcp/jwt', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          server_name: serverName,
          jwt_token: jwtToken.trim()
        })
      })

      const data = await response.json()

      if (!response.ok) {
        throw new Error(data.detail || `HTTP ${response.status}`)
      }

      setSuccess(true)
      setTimeout(() => {
        onSuccess?.()
        handleClose()
      }, 1500)

    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
    }
  }

  const handleClose = () => {
    setJwtToken('')
    setError(null)
    setSuccess(false)
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-gray-800 rounded-lg p-6 max-w-2xl w-full mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Key className="w-5 h-5 text-amber-400" />
            <h2 className="text-lg font-semibold">Upload JWT Token</h2>
          </div>
          <button
            onClick={handleClose}
            className="text-gray-400 hover:text-gray-300 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="mb-4">
          <p className="text-sm text-gray-400 mb-2">
            Server: <span className="text-white font-medium">{serverName}</span>
          </p>
          <p className="text-xs text-gray-500">
            Upload a JWT token to authenticate with this MCP server. The token will be encrypted and stored securely.
          </p>
        </div>

        {success ? (
          <div className="bg-green-900/20 border border-green-700 rounded-lg p-4 mb-4 flex items-center gap-3">
            <CheckCircle className="w-5 h-5 text-green-400" />
            <div>
              <p className="text-green-400 font-medium">JWT Token Uploaded Successfully</p>
              <p className="text-sm text-green-300/70">The token is now stored and will be used for authentication.</p>
            </div>
          </div>
        ) : (
          <>
            <div className="mb-4">
              <label className="block text-sm font-medium mb-2">
                JWT Token
              </label>
              <textarea
                value={jwtToken}
                onChange={(e) => setJwtToken(e.target.value)}
                placeholder="Paste your JWT token here (e.g., eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...)"
                className="w-full h-32 px-3 py-2 bg-gray-700 border border-gray-600 rounded-lg font-mono text-sm resize-vertical focus:outline-none focus:ring-2 focus:ring-amber-500"
                disabled={uploading}
              />
              <p className="text-xs text-gray-500 mt-1">
                The token should start with "eyJ" and contain three parts separated by dots.
              </p>
            </div>

            {error && (
              <div className="bg-red-900/20 border border-red-700 rounded-lg p-3 mb-4 flex items-start gap-2">
                <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-red-400 font-medium">Upload Failed</p>
                  <p className="text-sm text-red-300/70">{error}</p>
                </div>
              </div>
            )}

            <div className="bg-blue-900/20 border border-blue-700 rounded-lg p-3 mb-4">
              <p className="text-sm text-blue-300">
                <strong>Security Note:</strong> Your JWT token will be encrypted using Fernet encryption before storage. 
                The token is stored separately for each user and can only be accessed by you.
              </p>
            </div>

            <div className="flex gap-3 justify-end">
              <button
                onClick={handleClose}
                className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded-lg transition-colors"
                disabled={uploading}
              >
                Cancel
              </button>
              <button
                onClick={handleUpload}
                disabled={uploading || !jwtToken.trim()}
                className="px-4 py-2 bg-amber-600 hover:bg-amber-700 disabled:bg-amber-900 disabled:opacity-50 rounded-lg transition-colors flex items-center gap-2"
              >
                {uploading ? (
                  <>
                    <div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    Uploading...
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4" />
                    Upload JWT
                  </>
                )}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default JWTUploadDialog
