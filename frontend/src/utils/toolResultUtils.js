// Helpers that prepare MCP tool arguments / results for display in the chat UI.
//
// Extracted from Message.jsx. Behavior is unchanged from the inline versions.

export const filterArgumentsForDisplay = (args) => {
  if (!args || typeof args !== 'object') return args

  const filteredArgs = { ...args }

  if (filteredArgs.file_data_base64) {
    const dataSize = filteredArgs.file_data_base64.length
    filteredArgs.file_data_base64 = `[File data: ${dataSize} characters - hidden for display]`
  }

  return filteredArgs
}

export const processToolResult = (result) => {
  if (!result) return result

  if (typeof result === 'string') {
    try {
      const parsed = JSON.parse(result)
      return processToolResult(parsed)
    } catch {
      return result
    }
  }

  if (typeof result === 'object') {
    const processed = { ...result }

    if (processed.artifacts && Array.isArray(processed.artifacts)) {
      processed.artifacts = processed.artifacts.map(artifact => ({
        name: artifact.name,
        mime: artifact.mime,
        b64: `[File data: ${artifact.b64.length} characters - hidden for display]`
      }))
      processed._artifacts_download_available = true
    }
    else if (processed.returned_files && Array.isArray(processed.returned_files)) {
      processed.returned_files = processed.returned_files.map(file => ({
        filename: file.filename,
        content_base64: `[File data: ${file.content_base64.length} characters - hidden for display]`
      }))
      processed._multiple_files_download_available = true
    } else if (processed.returned_file_base64 && processed.returned_file_name) {
      const dataSize = processed.returned_file_base64.length
      processed.returned_file_base64 = `[File data: ${dataSize} characters - hidden for display]`
      processed._file_download_available = true
    }

    if (processed.returned_file_contents && Array.isArray(processed.returned_file_contents)) {
      processed.returned_file_contents = processed.returned_file_contents.map(content =>
        `[File data: ${content.length} characters - hidden for display]`
      )
    }

    Object.keys(processed).forEach(key => {
      if (key.includes('base64') && key !== 'returned_file_base64') {
        if (typeof processed[key] === 'string' && processed[key].length > 100) {
          processed[key] = `[Base64 data: ${processed[key].length} characters - hidden for display]`
        }
      }
    })

    return processed
  }

  return result
}

const MIME_TYPES = {
  'pdf': 'application/pdf',
  'txt': 'text/plain',
  'json': 'application/json',
  'csv': 'text/csv',
  'png': 'image/png',
  'jpg': 'image/jpeg',
  'jpeg': 'image/jpeg',
  'gif': 'image/gif',
  'doc': 'application/msword',
  'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'py': 'text/x-python',
  'js': 'text/javascript',
  'html': 'text/html',
  'css': 'text/css',
  'xml': 'application/xml'
}

export const downloadReturnedFile = (filename, base64Data) => {
  try {
    const byteCharacters = atob(base64Data)
    const byteNumbers = new Array(byteCharacters.length)
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i)
    }
    const byteArray = new Uint8Array(byteNumbers)

    const extension = filename.split('.').pop()?.toLowerCase()
    const mimeType = (extension && MIME_TYPES[extension]) || 'application/octet-stream'

    const blob = new Blob([byteArray], { type: mimeType })

    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)

    setTimeout(() => URL.revokeObjectURL(url), 100)

  } catch (error) {
    console.error('Error downloading file:', error)
    alert('Failed to download file. Please try again.')
  }
}
