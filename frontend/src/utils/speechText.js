// Turn an assistant message into text worth reading aloud (issue #1251).
//
// Markdown read literally is noise ("asterisk asterisk"), and a code block
// read character by character is worse, so markup is dropped and code
// blocks are replaced by a short spoken placeholder.

const CODE_PLACEHOLDER = ' (code block omitted) '

export const toSpeechText = (markdown) => {
  if (typeof markdown !== 'string') return ''
  return markdown
    .replace(/```[\s\S]*?(```|$)/g, CODE_PLACEHOLDER)
    .replace(/~~~[\s\S]*?(~~~|$)/g, CODE_PLACEHOLDER)
    .replace(/<[^>]+>/g, ' ')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*([-*+]|\d+[.)])\s+/gm, '')
    .replace(/^\s*([-*_]\s*){3,}$/gm, '')
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, '')
    .replace(/\|/g, ' ')
    .replace(/(\*\*|__|\*|_|~~)(?=\S)([\s\S]*?\S)\1/g, '$2')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/**
 * Fit `text` to the server's length limit, cutting at the last sentence end
 * (or, failing that, the last word) that fits. Returns the text to send and
 * whether anything was left out.
 */
export const fitSpeechText = (text, maxChars) => {
  const limit = Number(maxChars)
  if (!Number.isFinite(limit) || limit <= 0 || text.length <= limit) {
    return { text, truncated: false }
  }
  const head = text.slice(0, limit)
  const sentenceEnd = Math.max(head.lastIndexOf('. '), head.lastIndexOf('! '), head.lastIndexOf('? '), head.lastIndexOf('\n'))
  // Do not give up most of the allowance for the sake of a clean ending.
  const cut = sentenceEnd > limit * 0.5 ? sentenceEnd + 1 : (head.lastIndexOf(' ') > limit * 0.5 ? head.lastIndexOf(' ') : limit)
  return { text: head.slice(0, cut).trim(), truncated: true }
}
