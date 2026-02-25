/**
 * Tests for LaTeX pre-processing helpers.
 */
import { describe, it, expect } from 'vitest'
import { preProcessLatex, restoreLatexPlaceholders } from '../utils/latexPreprocessor'

describe('preProcessLatex', () => {
  it('renders display math with \\[...\\] delimiters', () => {
    const input = 'before \\[x^2\\] after'
    const { result, placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
    // Placeholder token present in result
    expect(result).toContain('LATEX_0_LATEXEND')
    expect(result).toContain('before')
    expect(result).toContain('after')
  })

  it('renders display math with $$...$$ delimiters', () => {
    const input = '$$ax^2+bx+c=0$$'
    const { result, placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
    expect(result).toContain('LATEX_0_LATEXEND')
  })

  it('renders inline math with \\(...\\) delimiters', () => {
    const input = 'The value is \\(x^2\\) units.'
    const { placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
  })

  it('renders inline math with single $...$ delimiters', () => {
    const input = 'The value is $x^2$ units.'
    const { placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
  })

  it('renders ```latex code blocks as KaTeX display math', () => {
    const input = '```latex\nax^{2}+bx+c=0\n```'
    const { result, placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
    expect(result).toContain('LATEX_0_LATEXEND')
  })

  it('renders ```tex code blocks as KaTeX display math', () => {
    const input = '```tex\nx = \\frac{-b}{2a}\n```'
    const { placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
  })

  it('does not render LaTeX inside non-latex fenced code blocks', () => {
    const input = '```python\n# $x^2$\n```'
    const { result, placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(0)
    expect(result).toContain('# $x^2$')
  })

  it('does not render LaTeX inside inline code spans', () => {
    const input = 'use `$x$` for inline math'
    const { result, placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(0)
    expect(result).toContain('`$x$`')
  })

  it('handles multiline display math', () => {
    const input = '\\[\nax^{2}+bx+c=0\n\\]'
    const { placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(1)
    expect(placeholders[0]).toContain('katex')
  })

  it('handles multiple LaTeX expressions in one message', () => {
    const input = 'First: \\[a\\] and second: \\[b\\]'
    const { result, placeholders } = preProcessLatex(input)

    expect(placeholders).toHaveLength(2)
    expect(result).toContain('LATEX_0_LATEXEND')
    expect(result).toContain('LATEX_1_LATEXEND')
  })
})

describe('restoreLatexPlaceholders', () => {
  it('substitutes LATEX_N_LATEXEND tokens with rendered HTML', () => {
    const html = '<p>LATEX_0_LATEXEND</p>'
    const placeholders = ['<span class="katex">x^2</span>']
    const result = restoreLatexPlaceholders(html, placeholders)

    expect(result).toBe('<p><span class="katex">x^2</span></p>')
  })

  it('returns empty string for out-of-range indices', () => {
    const html = 'LATEX_5_LATEXEND'
    const result = restoreLatexPlaceholders(html, [])

    expect(result).toBe('')
  })
})
