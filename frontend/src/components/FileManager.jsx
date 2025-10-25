import { useState, useEffect } from 'react'
import { 
  File, 
  Image, 
  Database, 
  FileText, 
  Code, 
  Download, 
  Trash2,
  Tag
} from 'lucide-react'

const FileManager = ({ files, onDownloadFile, onDeleteFile, taggedFiles, onToggleFileTag }) => {


  const getFileIcon = (file) => {
    switch (file.type) {
      case 'code':
        return <Code className="w-4 h-4 text-blue-400" />
      case 'image':
        return <Image className="w-4 h-4 text-green-400" />
      case 'data':
        return <Database className="w-4 h-4 text-yellow-400" />
      case 'document':
        return <FileText className="w-4 h-4 text-red-400" />
      default:
        return <File className="w-4 h-4 text-gray-400" />
    }
  }


  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 B'
    const k = 1024
    const sizes = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  if (!files || files.total_files === 0) {
    return (
      <div className="text-gray-400 text-center py-12 px-6">
        <File className="w-12 h-12 mx-auto mb-4 opacity-50" />
        <div className="text-lg mb-4">No files in this session</div>
        <p className="text-gray-500">
          Upload files or use tools that generate files to see them here
        </p>
      </div>
    )
  }

  return (
    <div>
      {/* Section Header */}
      <div className="mb-4">
        <h3 className="text-lg font-semibold text-white">
          Session Files ({files.total_files})
        </h3>
        <p className="text-sm text-gray-400 mt-1">
          All files from your current chat session
        </p>
      </div>

      <div className="space-y-3">
        {files.files.map((file, index) => (
          <div 
            key={`${file.filename}-${index}`}
            className="bg-gray-700 rounded-lg overflow-hidden"
          >
            <div className="p-4 flex items-center gap-4">
              {/* File Icon */}
              <div className="bg-gray-600 rounded-lg p-3 flex-shrink-0">
                {getFileIcon(file)}
              </div>
              
              {/* File Content */}
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between mb-1">
                  <h3 className="text-white font-semibold text-base truncate font-mono">
                    {file.filename}
                  </h3>
                  {taggedFiles?.has(file.filename) && (
                    <span className="px-2 py-1 bg-green-600 text-xs rounded text-white flex-shrink-0">
                      Tagged
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 text-sm text-gray-400">
                  <span>{formatFileSize(file.size)}</span>
                  <span>•</span>
                  <span className="uppercase">{file.extension}</span>
                </div>
              </div>
              
              {/* Action Buttons */}
              <div className="flex items-center gap-2 flex-shrink-0">
                <button
                  onClick={() => onToggleFileTag?.(file.filename)}
                  className={`p-2 rounded-lg transition-colors ${
                    taggedFiles?.has(file.filename)
                      ? 'bg-green-600 hover:bg-green-700 text-white'
                      : 'bg-gray-600 hover:bg-gray-500 text-gray-200'
                  }`}
                  title={taggedFiles?.has(file.filename) ? "Remove from chat context" : "Tag for chat context"}
                >
                  <Tag className="w-4 h-4" />
                </button>
                
                <button
                  onClick={() => onDownloadFile?.(file.filename)}
                  className="p-2 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-colors"
                  title="Download file"
                >
                  <Download className="w-4 h-4" />
                </button>
                
                <button
                  onClick={() => onDeleteFile?.(file.filename)}
                  className="p-2 rounded-lg bg-red-600 hover:bg-red-700 text-white transition-colors"
                  title="Delete file"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default FileManager