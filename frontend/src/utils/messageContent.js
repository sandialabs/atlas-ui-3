// Content-shaping helpers run on raw message text before it is handed to `marked`.
//
// Extracted from Message.jsx. Behavior is unchanged from the inline versions.

const processFileReferences = (content) => {
  return content.replace(
    /@file\s+([^\s]+)/g,
    '<span class="inline-flex items-center px-2 py-1 rounded-md bg-green-900/30 border border-green-500/30 text-green-400 text-sm font-medium">@file $1</span>'
  )
}

const convertBulletListsToMarkdown = (content) => {
  return content.replace(/^(\s*)[•◦▪▫‣]\s+(.+)$/gm, '$1- $2')
}

export const processMessageContent = (content) => {
  let processedContent = ''

  if (typeof content === 'string') {
    processedContent = content
  } else if (content && typeof content === 'object') {
    if (content.raw && typeof content.raw === 'string') {
      processedContent = content.raw
    } else if (content.text && typeof content.text === 'string') {
      processedContent = content.text
    } else {
      try {
        processedContent = JSON.stringify(content, null, 2)
      } catch {
        processedContent = String(content)
      }
    }
  } else {
    processedContent = String(content || '')
  }

  processedContent = convertBulletListsToMarkdown(processedContent)
  processedContent = processFileReferences(processedContent)

  return processedContent
}
