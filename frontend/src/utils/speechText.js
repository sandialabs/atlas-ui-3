// Turn an assistant message into text worth reading aloud (issue #1251).
//
// Markdown read literally is noise ("asterisk asterisk"), and a code block
// read character by character is worse, so markup is dropped and code
// blocks are replaced by a short spoken placeholder.

const CODE_PLACEHOLDER = ' (code block omitted) '

// Private-use characters that cannot occur in a message; they bracket the
// index of an inline code span while the surrounding markup is processed.
const CODE_OPEN = '\uE000'
const CODE_CLOSE = '\uE001'

export const toSpeechText = (markdown) => {
  if (typeof markdown !== 'string') return ''
  // Inline code is taken out first and put back last, untouched. What is
  // inside backticks is literal: `Array<String>` is not a tag and `a[1](2)`
  // is not a link, and reading it through the markup passes would delete
  // exactly the part the author marked as code.
  const codeSpans = []
  const withoutCode = markdown
    .replace(/```[\s\S]*?(```|$)/g, CODE_PLACEHOLDER)
    .replace(/~~~[\s\S]*?(~~~|$)/g, CODE_PLACEHOLDER)
    .replace(/`([^`\n]+)`/g, (_match, code) => `${CODE_OPEN}${codeSpans.push(code) - 1}${CODE_CLOSE}`)
  return withoutCode
    // Only tag-shaped spans (<br>, </div>, <a href="...">). A bare "<" in
    // prose, as in "if a < b > c", is a comparison and stays.
    .replace(/<\/?[A-Za-z][A-Za-z0-9-]*(\s[^<>]*)?\/?>/g, ' ')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}#{1,6}\s+/gm, '')
    .replace(/^\s{0,3}>\s?/gm, '')
    .replace(/^\s*([-*+]|\d+[.)])\s+/gm, '')
    .replace(/^\s*([-*_]\s*){3,}$/gm, '')
    .replace(/^\s*\|?[\s:|-]+\|[\s:|-]*$/gm, '')
    .replace(/\|/g, ' ')
    .replace(/(\*\*|__|\*|_|~~)(?=\S)([\s\S]*?\S)\1/g, '$2')
    .replace(new RegExp(`${CODE_OPEN}(\\d+)${CODE_CLOSE}`, 'g'), (_match, index) => codeSpans[Number(index)])
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
  // Never end on the first half of a surrogate pair: half an emoji is not a
  // character, and a lone surrogate is not valid text to send.
  const isHighSurrogate = code => code >= 0xD800 && code <= 0xDBFF
  const head = text.slice(0, isHighSurrogate(text.charCodeAt(limit - 1)) ? limit - 1 : limit)
  const sentenceEnd = Math.max(head.lastIndexOf('. '), head.lastIndexOf('! '), head.lastIndexOf('? '), head.lastIndexOf('\n'))
  // Do not give up most of the allowance for the sake of a clean ending.
  const cut = sentenceEnd > limit * 0.5 ? sentenceEnd + 1 : (head.lastIndexOf(' ') > limit * 0.5 ? head.lastIndexOf(' ') : head.length)
  return { text: head.slice(0, cut).trim(), truncated: true }
}
