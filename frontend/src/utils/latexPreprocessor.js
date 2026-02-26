import katex from 'katex'

// Pre-process LaTeX math expressions before markdown rendering.
// Fenced code blocks are temporarily replaced with placeholders so that
// LaTeX inside code blocks is never rendered.  The remaining text is scanned
// for LaTeX delimiters (\[…\], $$…$$, \(…\), $…$) and replaced with
// KaTeX-rendered HTML snippets stored in a sidecar array.  After marked
// converts the rest of the text to HTML the placeholders are substituted
// back in by restoreLatexPlaceholders.
export const preProcessLatex = (content) => {
  const placeholders = []

  // Render ```latex / ```tex / ```math code blocks as KaTeX display math
  // instead of treating them as regular code blocks.
  const renderLatex = (latex, displayMode) => {
    try {
      return katex.renderToString(latex.trim(), { displayMode, throwOnError: false, output: 'html' })
    } catch {
      return latex
    }
  }

  let result = content.replace(/```(?:latex|tex|math)\s*\n([\s\S]*?)```/gi, (_, latex) => {
    const id = `LATEX_${placeholders.length}_LATEXEND`
    placeholders.push(renderLatex(latex, true))
    return id
  })

  // Temporarily pull out remaining fenced code blocks so we never touch their contents.
  const codeBlocks = []
  result = result.replace(/```[\s\S]*?```/g, (match) => {
    const id = `CODEBLOCK_${codeBlocks.length}_CODEEND`
    codeBlocks.push(match)
    return id
  })

  // Also protect inline code spans.
  const inlineCodes = []
  result = result.replace(/`[^`\n]+`/g, (match) => {
    const id = `INLINECODE_${inlineCodes.length}_INLINECODEEND`
    inlineCodes.push(match)
    return id
  })

  // Display math: \[...\]
  result = result.replace(/\\\[([\s\S]*?)\\\]/g, (_, latex) => {
    const id = `LATEX_${placeholders.length}_LATEXEND`
    placeholders.push(renderLatex(latex, true))
    return id
  })

  // Display math: $$...$$
  result = result.replace(/\$\$([\s\S]*?)\$\$/g, (_, latex) => {
    const id = `LATEX_${placeholders.length}_LATEXEND`
    placeholders.push(renderLatex(latex, true))
    return id
  })

  // Inline math: \(...\)
  result = result.replace(/\\\(([\s\S]*?)\\\)/g, (_, latex) => {
    const id = `LATEX_${placeholders.length}_LATEXEND`
    placeholders.push(renderLatex(latex, false))
    return id
  })

  // Inline math: $...$  (single dollar, not preceded/followed by another $)
  result = result.replace(/(?<!\$)\$(?!\$)([^$\n]+?)(?<!\$)\$(?!\$)/g, (_, latex) => {
    const id = `LATEX_${placeholders.length}_LATEXEND`
    placeholders.push(renderLatex(latex, false))
    return id
  })

  // Restore code blocks and inline code before handing off to marked.
  result = result.replace(/CODEBLOCK_(\d+)_CODEEND/g, (_, i) => codeBlocks[parseInt(i)])
  result = result.replace(/INLINECODE_(\d+)_INLINECODEEND/g, (_, i) => inlineCodes[parseInt(i)])

  return { result, placeholders }
}

// Replace LATEX_N_LATEXEND tokens in the already-markedified HTML with the
// pre-rendered KaTeX HTML snippets.
export const restoreLatexPlaceholders = (html, placeholders) => {
  return html.replace(/LATEX_(\d+)_LATEXEND/g, (_, i) => placeholders[parseInt(i)] ?? '')
}
