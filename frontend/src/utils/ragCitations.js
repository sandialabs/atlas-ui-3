// RAG citation rendering helpers.
//
// These transform an HTML string produced by `marked` into one where:
//   - `[N]` markers become clickable source chips (except inside <code>/<pre>)
//   - the "References" section becomes a collapsible <details> with anchor IDs
//   - source labels (from the References section) power a compact summary
//
// Extracted from Message.jsx. Behavior is unchanged from the inline versions.

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
