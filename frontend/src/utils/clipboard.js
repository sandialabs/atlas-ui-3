// Clipboard helpers for code blocks and message content.
//
// Extracted from Message.jsx to keep that component focused on rendering.
// Behavior is unchanged from the inline versions.

const showCopySuccess = (button) => {
  const originalHTML = button.innerHTML

  button.innerHTML = '<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg>'
  button.classList.add('bg-green-600', 'border-green-500')
  button.classList.remove('bg-gray-700', 'border-gray-600')

  setTimeout(() => {
    button.innerHTML = originalHTML
    button.classList.remove('bg-green-600', 'border-green-500')
    button.classList.add('bg-gray-700', 'border-gray-600')
  }, 2000)
}

const fallbackCopy = (text, button) => {
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.left = '-999999px'
    textArea.style.top = '-999999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()

    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)

    if (successful) {
      showCopySuccess(button)
    } else {
      console.error('Fallback copy failed')
    }
  } catch (err) {
    console.error('Fallback copy error: ', err)
  }
}

export const copyCodeBlock = (button) => {
  try {
    const container = button.closest('.code-block-container')
    if (!container) {
      console.error('Could not find code block container')
      return
    }

    const codeBlock = container.querySelector('code')
    if (!codeBlock) {
      console.error('Could not find code element')
      return
    }

    const text = codeBlock.textContent || codeBlock.innerText || ''

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(() => {
        showCopySuccess(button)
      }).catch(err => {
        console.error('Failed to copy with Clipboard API: ', err)
        fallbackCopy(text, button)
      })
    } else {
      fallbackCopy(text, button)
    }
  } catch (err) {
    console.error('Error in copyCodeBlock: ', err)
  }
}

const showMessageCopySuccess = (button) => {
  const originalClasses = button.className

  button.classList.remove('bg-gray-700', 'hover:bg-gray-600', 'border-gray-600', 'text-gray-200')
  button.classList.add('bg-green-600', 'hover:bg-green-700', 'border-green-500', 'text-white')

  setTimeout(() => {
    button.className = originalClasses
  }, 2000)
}

const fallbackMessageCopy = (text, button) => {
  try {
    const textArea = document.createElement('textarea')
    textArea.value = text
    textArea.style.position = 'fixed'
    textArea.style.left = '-999999px'
    textArea.style.top = '-999999px'
    document.body.appendChild(textArea)
    textArea.focus()
    textArea.select()

    const successful = document.execCommand('copy')
    document.body.removeChild(textArea)

    if (successful) {
      showMessageCopySuccess(button)
    } else {
      console.error('Fallback message copy failed')
    }
  } catch (err) {
    console.error('Fallback message copy error: ', err)
  }
}

export const copyMessageContent = (content, button) => {
  try {
    let textToCopy = ''

    if (typeof content === 'string') {
      textToCopy = content
    } else if (content && typeof content === 'object') {
      if (content.raw && typeof content.raw === 'string') {
        textToCopy = content.raw
      } else if (content.text && typeof content.text === 'string') {
        textToCopy = content.text
      } else {
        textToCopy = JSON.stringify(content, null, 2)
      }
    } else {
      textToCopy = String(content || '')
    }

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(textToCopy).then(() => {
        showMessageCopySuccess(button)
      }).catch(err => {
        console.error('Failed to copy message with Clipboard API: ', err)
        fallbackMessageCopy(textToCopy, button)
      })
    } else {
      fallbackMessageCopy(textToCopy, button)
    }
  } catch (err) {
    console.error('Error in copyMessageContent: ', err)
  }
}
