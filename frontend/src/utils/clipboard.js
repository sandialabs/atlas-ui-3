// Clipboard helpers for code blocks, message content, and canvas content.
//
// Extracted from Message.jsx to keep that component focused on rendering.
// Every copy path prefers the async Clipboard API and falls back to the
// legacy hidden-textarea + execCommand trick (insecure contexts, older
// browsers, denied permissions). Every path flashes visible success or
// failure feedback on the button that triggered it (#1151).

const FLASH_MS = 2000

// A flash temporarily restyles the button (and, for code blocks, swaps its
// markup for a check or cross icon). The original class list -- and markup,
// when swapped -- is captured once per flash and restored verbatim. If a
// previous flash is still running (two quick clicks, or a success followed by
// a failure), its pending restore is cancelled and the state it captured is
// reused, so the button can never be left stuck green or red.
const beginFlash = (button, { swapMarkup }) => {
  if (button._copyFlashTimer) clearTimeout(button._copyFlashTimer)
  if (button._copyOriginalClassName === undefined) {
    button._copyOriginalClassName = button.className
    if (swapMarkup) button._copyOriginalHTML = button.innerHTML
  }
  return { html: button._copyOriginalHTML, className: button._copyOriginalClassName }
}

const restoreAfter = (button, original) => {
  button._copyFlashTimer = setTimeout(() => {
    button._copyFlashTimer = null
    delete button._copyOriginalHTML
    delete button._copyOriginalClassName
    // Message copy buttons are React-rendered: their children must never be
    // replaced, so markup is only restored when a flash swapped it.
    if (original.html !== undefined) button.innerHTML = original.html
    button.className = original.className
  }, FLASH_MS)
}

// Neutral classes swapped out during a flash. The hover background must go
// too: the pointer is still over the button right after the click, and
// `hover:bg-gray-600` would otherwise paint over the green/red flash so only
// the thin border changed colour (#1151).
const NEUTRAL_CLASSES = ['bg-gray-700', 'hover:bg-gray-600', 'border-gray-600']

const flashButton = (button, flashClasses, { iconPath = null, extraNeutral = [], extraFlash = [] } = {}) => {
  const original = beginFlash(button, { swapMarkup: iconPath !== null })

  if (iconPath !== null) {
    button.innerHTML = `<svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="${iconPath}"></path></svg>`
  }
  button.className = original.className
  button.classList.remove(...NEUTRAL_CLASSES, ...extraNeutral)
  button.classList.add(...flashClasses, ...extraFlash)

  restoreAfter(button, original)
}

const SUCCESS_CLASSES = ['bg-green-600', 'hover:bg-green-700', 'border-green-500']
const ERROR_CLASSES = ['bg-red-600', 'hover:bg-red-700', 'border-red-500']
const CHECK_ICON = 'M5 13l4 4L19 7'
const CROSS_ICON = 'M6 18L18 6M6 6l12 12'

const showCopySuccess = (button) => flashButton(button, SUCCESS_CLASSES, { iconPath: CHECK_ICON })

const showCopyError = (button) => flashButton(button, ERROR_CLASSES, { iconPath: CROSS_ICON })

// Legacy fallback for insecure contexts and denied Clipboard API permissions.
// Returns whether the copy actually succeeded so callers can surface a
// visible failure when both paths fail (#1151).
const execCommandCopy = (text) => {
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
    if (!successful) {
      console.error('execCommand copy failed')
    }
    return successful
  } catch (err) {
    console.error('execCommand copy error: ', err)
    return false
  }
}

// Shared copy core: try the async Clipboard API, fall back to execCommand,
// then flash success or failure feedback on the triggering button (#1151).
const copyWithFeedback = (text, button, successFeedback, errorFeedback) => {
  const report = (success) => (success ? successFeedback(button) : errorFeedback(button))
  const viaFallback = (apiError) => {
    if (apiError) {
      console.error('Clipboard API copy failed, trying execCommand fallback: ', apiError)
    }
    report(execCommandCopy(text))
  }

  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(
      () => report(true),
      (err) => viaFallback(err)
    )
  } else {
    viaFallback(null)
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

    // textContent reads every character of the code element regardless of
    // scroll position or on-screen visibility, so a long block that is
    // scrolled past in the transcript still copies in full (#1151).
    const text = codeBlock.textContent || codeBlock.innerText || ''

    copyWithFeedback(text, button, showCopySuccess, showCopyError)
  } catch (err) {
    console.error('Error in copyCodeBlock: ', err)
  }
}

const MESSAGE_TEXT = { extraNeutral: ['text-gray-200'], extraFlash: ['text-white'] }

const showMessageCopySuccess = (button) => flashButton(button, SUCCESS_CLASSES, MESSAGE_TEXT)

const showMessageCopyError = (button) => flashButton(button, ERROR_CLASSES, MESSAGE_TEXT)

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

    copyWithFeedback(textToCopy, button, showMessageCopySuccess, showMessageCopyError)
  } catch (err) {
    console.error('Error in copyMessageContent: ', err)
  }
}

// Copy plain text without any button feedback. CanvasPanel uses this and
// renders its own copied/failed state in React (#1151).
export const copyTextToClipboard = (text) => {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    return navigator.clipboard.writeText(text).then(
      () => true,
      (err) => {
        console.error('Clipboard API copy failed: ', err)
        return execCommandCopy(text)
      }
    )
  }
  return Promise.resolve(execCommandCopy(text))
}
