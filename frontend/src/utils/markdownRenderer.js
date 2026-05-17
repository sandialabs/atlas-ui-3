// Markdown pipeline for assistant messages: hljs language registration,
// a custom `marked` renderer for code + links, and the DOMPurify config that
// permits KaTeX's HTML/MathML output.
//
// Extracted from Message.jsx. Behavior is unchanged from the inline version.
// Importing this module has the side-effect of configuring the shared `marked`
// singleton — import it once from the component that renders messages.

import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github-dark.css'

import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import go from 'highlight.js/lib/languages/go'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import css from 'highlight.js/lib/languages/css'
import html from 'highlight.js/lib/languages/xml'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import sql from 'highlight.js/lib/languages/sql'
import bash from 'highlight.js/lib/languages/bash'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('rs', rust)
hljs.registerLanguage('go', go)
hljs.registerLanguage('golang', go)
hljs.registerLanguage('java', java)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('c++', cpp)
hljs.registerLanguage('css', css)
hljs.registerLanguage('html', html)
hljs.registerLanguage('xml', html)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('sh', bash)

// DOMPurify configuration that permits KaTeX-generated HTML.
// KaTeX renders almost exclusively with <span> (allowed by default) but also
// uses <svg>, <path>, and a handful of MathML elements for some symbols.
export const DOMPURIFY_CONFIG = {
  ADD_TAGS: ['annotation', 'semantics', 'math', 'mrow', 'mi', 'mo', 'mn', 'msup', 'msub', 'mfrac', 'msqrt', 'mspace', 'mtext', 'details', 'summary'],
  ADD_ATTR: ['encoding', 'mathvariant', 'stretchy', 'fence', 'separator', 'lspace', 'rspace', 'data-ref', 'data-citation-target', 'data-section-ref', 'role', 'tabindex', 'aria-label'],
}

const renderer = new marked.Renderer()

renderer.link = function(href, title, text) {
  if (typeof href === 'object' && href !== null) {
    title = href.title
    text = href.text || href.tokens?.map(t => t.raw).join('') || ''
    href = href.href
  }
  const escTitle = title ? title.replace(/&/g, '&amp;').replace(/"/g, '&quot;') : ''
  const titleAttr = escTitle ? ` title="${escTitle}"` : ''
  if (href && href.startsWith('#')) {
    return `<a href="${href}"${titleAttr}>${text}</a>`
  }
  return `<a href="${href}" target="_blank" rel="noopener noreferrer"${titleAttr}>${text}</a>`
}

// Restrict language identifiers to a safe charset before interpolating into
// HTML. The output is later sanitized by DOMPurify, but keeping the markup
// well-formed avoids surprises and defends the class-attribute / label spot
// where a fence info string like ``` lang"></span><... could otherwise land.
const sanitizeLanguageTag = (lang) => {
  if (!lang || typeof lang !== 'string') return ''
  return lang.toLowerCase().replace(/[^a-z0-9_+#-]/g, '').slice(0, 32)
}

renderer.code = function(code, language) {
  let codeString = ''
  let actualLanguage = language

  if (typeof code === 'string') {
    codeString = code
  } else if (code && typeof code === 'object') {
    if (code.text && typeof code.text === 'string') {
      codeString = code.text
      if (code.lang && !actualLanguage) {
        actualLanguage = code.lang
      }
    } else if (code.raw && typeof code.raw === 'string') {
      const rawMatch = code.raw.match(/```(\w*)\n([\s\S]*?)\n```/)
      if (rawMatch) {
        codeString = rawMatch[2] || ''
        if (rawMatch[1] && !actualLanguage) {
          actualLanguage = rawMatch[1]
        }
      } else {
        codeString = code.raw
      }
    } else {
      try {
        codeString = JSON.stringify(code, null, 2)
        actualLanguage = 'json'
      } catch {
        codeString = String(code)
      }
    }
  } else {
    codeString = String(code || '')
  }

  let highlightedCode = ''
  if (actualLanguage && hljs.getLanguage(actualLanguage)) {
    try {
      const result = hljs.highlight(codeString, { language: actualLanguage })
      highlightedCode = result.value
    } catch (e) {
      console.warn('Highlight.js error for language', actualLanguage, e)
      highlightedCode = codeString.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;')
                          .replace(/"/g, '&quot;')
                          .replace(/'/g, '&#39;')
    }
  } else {
    try {
      const result = hljs.highlightAuto(codeString)
      highlightedCode = result.value
      if (result.language && !actualLanguage) {
        actualLanguage = result.language
      }
    } catch (e) {
      console.warn('Highlight.js auto-detection error', e)
      highlightedCode = codeString.replace(/&/g, '&amp;')
                          .replace(/</g, '&lt;')
                          .replace(/>/g, '&gt;')
                          .replace(/"/g, '&quot;')
                          .replace(/'/g, '&#39;')
    }
  }

  const safeLang = sanitizeLanguageTag(actualLanguage) || 'text'

  return `<div class="code-block-container relative bg-gray-900 rounded-lg my-4 border border-gray-700">
    <div class="flex items-center justify-between px-4 py-2 bg-gray-800 border-b border-gray-700">
      <span class="text-xs text-gray-400 font-medium uppercase tracking-wider">${safeLang}</span>
      <button
        class="copy-button bg-gray-700 hover:bg-gray-600 border border-gray-600 text-gray-200 px-3 py-1 rounded text-xs transition-all duration-200 cursor-pointer focus:outline-none focus:ring-2 focus:ring-blue-500"
        data-action="copy-code"
        title="Copy code to clipboard"
        type="button"
      >Copy</button>
    </div>
    <pre class="p-4 overflow-x-auto bg-gray-900 m-0"><code class="hljs language-${safeLang} text-sm leading-relaxed">${highlightedCode}</code></pre>
  </div>`
}

marked.setOptions({
  renderer: renderer,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(code, { language: lang }).value
      } catch (err) {
        console.warn('Highlight.js error:', err)
      }
    }
    try {
      return hljs.highlightAuto(code).value
    } catch (err) {
      console.warn('Highlight.js auto-detection error:', err)
      return code
    }
  },
  breaks: true,
  gfm: true
})

export { marked }
