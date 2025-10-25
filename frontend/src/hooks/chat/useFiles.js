import { useCallback, useEffect, useState } from 'react'

const initialSessionFiles = {
  total_files: 0,
  files: [],
  categories: { code: [], image: [], data: [], document: [], other: [] }
}

function getFileType(filename) {
  const extension = filename.toLowerCase().split('.').pop()
  if (['png', 'jpg', 'jpeg', 'gif', 'svg'].includes(extension)) return 'image'
  if (extension === 'pdf') return 'pdf'
  if (extension === 'html') return 'html'
  return 'other'
}

export function useFiles() {
  const [sessionFiles, setSessionFiles] = useState(initialSessionFiles)
  const [canvasContent, setCanvasContent] = useState('')
  const [canvasFiles, setCanvasFiles] = useState([])
  const [currentCanvasFileIndex, setCurrentCanvasFileIndex] = useState(0)
  const [customUIContent, setCustomUIContent] = useState(null)
  const [taggedFiles, setTaggedFiles] = useState(new Set())

  // load tagged files
  useEffect(() => {
    try {
      const saved = localStorage.getItem('chatui-tagged-files')
      if (saved) setTaggedFiles(new Set(JSON.parse(saved)))
    } catch {/* ignore */}
  }, [])

  const toggleFileTag = useCallback(filename => {
    setTaggedFiles(prev => {
      const next = new Set(prev)
      next.has(filename) ? next.delete(filename) : next.add(filename)
      localStorage.setItem('chatui-tagged-files', JSON.stringify([...next]))
      return next
    })
  }, [])

  const clearTaggedFiles = useCallback(() => {
    setTaggedFiles(new Set())
    localStorage.removeItem('chatui-tagged-files')
  }, [])

  const getTaggedFilesContent = useCallback(() => {
    const fileContents = {}
    for (const filename of taggedFiles) {
      if (sessionFiles.files.some(f => f.filename === filename)) {
        fileContents[filename] = `[File: ${filename} - included for context]`
      }
    }
    return fileContents
  }, [taggedFiles, sessionFiles.files])

  return {
    // session / files
    sessionFiles, setSessionFiles,
    canvasContent, setCanvasContent,
    canvasFiles, setCanvasFiles,
    currentCanvasFileIndex, setCurrentCanvasFileIndex,
    customUIContent, setCustomUIContent,
    taggedFiles, toggleFileTag, clearTaggedFiles,
    getTaggedFilesContent,
    getFileType
  }
}
