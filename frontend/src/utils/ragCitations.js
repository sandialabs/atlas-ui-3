// RAG citation rendering helpers.
//
// `processCitationBadges` turns `[N]` markers in an answer into clickable
// source chips (except inside <code>/<pre>). `renderReferencesSection` builds
// the collapsible references block those chips scroll to.
//
// The references used to be *scraped back out* of the rendered answer: the
// backend appended a `**References**` markdown block to the assistant's own
// message and `extractSourceLabels` re-read it with regexes over the HTML.
// That stopped working when search became a tool call (#862) -- nothing writes
// that block any more, and with the model free to search several times per turn
// there is no single RAG response to append it to. It was also why passage text
// had to be sanitized against masquerading as a reference entry: attacker- or
// data-controlled text sat in the same string the extractor parsed.
//
// Citations now arrive as data on `message.citations` (issue #874) and are
// rendered here. Nothing parses the answer text for them.

// Cap on rendered references. The backend stops numbering at 99 (see
// MAX_CITATION_NUMBER -- processCitationBadges below matches [\d{1,2}], so a
// three-digit marker would never become a chip). This is the render-side guard,
// so a replayed conversation carrying hand-edited metadata cannot produce an
// unbounded DOM.
const MAX_RENDERED_REFERENCES = 99

const escapeHtml = (value) =>
  String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')

// Only http(s) becomes an href. The backend drops other schemes too, but a
// reference can also arrive from persisted metadata, so the render path does
// not rely on that having happened.
const safeHref = (url) => {
  if (typeof url !== 'string') return null
  const trimmed = url.trim()
  return /^https?:\/\//i.test(trimmed) ? trimmed : null
}

/**
 * Build the collapsible references block for a message's citations.
 *
 * @param {Array<{n:number,filename?:string,url?:string,citation?:string,data_source?:string}>} citations
 * @param {string} scope - Per-message suffix keeping anchor IDs unique when
 *   several answers in one transcript cite overlapping numbers.
 * @returns {string} HTML, or '' when there is nothing to render.
 */
export const renderReferencesSection = (citations, scope = '') => {
  if (!Array.isArray(citations) || citations.length === 0) return ''
  const prefix = scope ? `rag-ref-${scope}` : 'rag-ref'

  const entries = citations
    .filter(c => c && typeof c.n === 'number')
    .slice(0, MAX_RENDERED_REFERENCES)
    .sort((a, b) => a.n - b.n)
  if (entries.length === 0) return ''

  const rows = entries.map(entry => {
    const label = escapeHtml(entry.filename || entry.citation || entry.url || `Source ${entry.n}`)
    const href = safeHref(entry.url)
    const linked = href
      ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : label
    const details = []
    if (entry.data_source) details.push(escapeHtml(entry.data_source))
    // The citation string is the backend's own formatted reference (an APA-ish
    // line, typically). Shown under the label rather than as it, so the label
    // stays a filename a reader can scan a list by.
    const detailHtml = details.length > 0
      ? `<span class="rag-ref-detail"> — ${details.join(', ')}</span>`
      : ''
    const citationHtml = entry.citation && entry.citation !== entry.filename
      ? `<div class="rag-ref-citation">${escapeHtml(entry.citation)}</div>`
      : ''
    // The passage text the backend matched. The markdown path showed this too
    // (as `rag-ref-snippet` blockquotes); dropping it would take the evidence
    // out of the expanded area and leave only a list of filenames.
    const snippets = Array.isArray(entry.snippets) ? entry.snippets.slice(0, 3) : []
    const snippetHtml = snippets
      .map(text => `<div class="rag-ref-snippet">${escapeHtml(text)}</div>`)
      .join('')
    return (
      `<li id="${prefix}-${entry.n}" class="rag-ref-entry">` +
      `<span class="rag-ref-num">${entry.n}.</span> ${linked}${detailHtml}${citationHtml}${snippetHtml}` +
      '</li>'
    )
  }).join('')

  const summary = entries
    .map(entry => {
      const label = escapeHtml(entry.filename || entry.citation || entry.url || `Source ${entry.n}`)
      return `<span class="rag-summary-ref">[${entry.n}]</span> ${label}`
    })
    .join('<span class="rag-summary-sep">,</span> ')

  return (
    '<details class="rag-references-collapse">' +
    `<summary class="rag-references-summary" aria-label="References: ${entries.length} sources">${summary}</summary>` +
    `<ol class="rag-references-list">${rows}</ol>` +
    '</details>'
  )
}

export const extractSourceLabels = (html) => {
  const labels = new Map()
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return labels

  const refsHtml = html.slice(refIdx)

  const numberedLinkPattern = /(\d{1,2})\.\s+<a[^>]*href="([^"]*)"[^>]*>([^<]+)<\/a>/g
  const liLinkPattern = /<li><a[^>]*href="([^"]*)"[^>]*>([^<]+)<\/a>/g
  const numberedPlainPattern = /(\d{1,2})\.\s+([^<—\n]+)/g
  const liPlainPattern = /<li>([^<—\n]+)/g

  let m, idx

  while ((m = numberedLinkPattern.exec(refsHtml)) !== null) {
    labels.set(m[1], { label: m[3].trim(), url: m[2] })
  }
  while ((m = numberedPlainPattern.exec(refsHtml)) !== null) {
    if (!labels.has(m[1])) {
      labels.set(m[1], { label: m[2].trim(), url: null })
    }
  }

  if (labels.size === 0) {
    idx = 1
    while ((m = liLinkPattern.exec(refsHtml)) !== null) {
      labels.set(String(idx++), { label: m[2].trim(), url: m[1] })
    }
    if (labels.size === 0) {
      idx = 1
      while ((m = liPlainPattern.exec(refsHtml)) !== null) {
        labels.set(String(idx++), { label: m[1].trim(), url: null })
      }
    }
  }

  return labels
}

export const processCitationBadges = (html, scope = '') => {
  // Track whether we are inside a <code> or <pre> block so we don't convert
  // array indices like `arr[1]` into citation badges. `scope` keeps anchor
  // IDs unique per message when multiple RAG responses share the chat.
  let insideCode = 0
  return html.replace(
    /(<\/?(?:code|pre)[^>]*>)|(<[^>]*>)|(?<!\]\()(\[(\d{1,2})\])(?!\()/gi,
    (match, codeTag, otherTag, bracket, num) => {
      if (codeTag) {
        if (codeTag[1] === '/') {
          insideCode = Math.max(0, insideCode - 1)
        } else {
          insideCode++
        }
        return codeTag
      }
      if (otherTag) return otherTag
      if (insideCode > 0) return match
      const refId = scope ? `rag-ref-${scope}-${num}` : `rag-ref-${num}`
      // Real <button> so Enter/Space activate the chip (native button semantics);
      // the parent Message onClick handler still picks it up via event delegation.
      return `<span class="rag-source-chip" data-ref="${num}"><button type="button" aria-label="Citation ${num}" class="rag-source-chip-inner rag-source-chip-numonly" data-citation-target="${refId}">${num}</button></span>`
    }
  )
}

export const processReferencesSection = (html, scope = '', sourceLabels = new Map()) => {
  const refIdx = html.indexOf('<strong>References</strong>')
  if (refIdx === -1) return html

  const before = html.slice(0, refIdx)
  const after = html.slice(refIdx)
  const prefix = scope ? `rag-ref-${scope}` : 'rag-ref'

  let liCounter = 0
  const anchored = after
    .replace(/<li>(\d{1,2})\.\s/g, (_, num) => `<li id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
    .replace(/<p>(\d{1,2})\.\s/g, (_, num) => `<p id="${prefix}-${num}" class="rag-ref-entry">${num}. `)
    .replace(/<li>(?!\d{1,2}\.\s)/g, () => {
      liCounter++
      return `<li id="${prefix}-${liCounter}" class="rag-ref-entry">`
    })

  const summaryParts = []
  const sorted = [...sourceLabels.entries()].sort((a, b) => Number(a[0]) - Number(b[0]))
  for (const [num, src] of sorted) {
    summaryParts.push(`<span class="rag-summary-ref">[${num}]</span> ${src.label}`)
  }
  const summaryText = summaryParts.length > 0
    ? summaryParts.join('<span class="rag-summary-sep">,</span> ')
    : 'Sources'

  const wrapped = anchored
    .replace(
      /(<p>)?<strong>References<\/strong>(<\/p>)?/,
      `<details class="rag-references-collapse"><summary class="rag-references-summary" aria-label="References: ${summaryParts.length} sources">${summaryText}</summary>`
    ) + '</details>'

  return before + wrapped
}
