import { useState } from 'react'
import { useTheme } from '../contexts/ThemeContext'

/**
 * Accessible, reusable controls driven by Retro Design System design tokens.
 * Decorative title-bar glyphs are intentionally not interactive. This is a
 * normal section, NOT a dialog; callers own their own modal/focus semantics.
 */
export function RetroWindow({ title, children, footer, onClose, className = '' }) {
  return (
    <section className={`atlas-retro-window ${className}`} aria-label={title}>
      <div className="atlas-retro-window-titlebar">
        <span className="atlas-retro-window-symbol" aria-hidden="true">▣</span>
        <h3 className="atlas-retro-window-title">{title}</h3>
        {onClose ? (
          <button
            type="button"
            className="atlas-retro-window-close"
            aria-label={'Close ' + title}
            onClick={onClose}
          >
            ×
          </button>
        ) : (
          <span className="atlas-retro-window-glyphs" aria-hidden="true">▤</span>
        )}
      </div>
      <div className="atlas-retro-window-content">{children}</div>
      {footer != null && <footer className="atlas-retro-window-footer">{footer}</footer>}
    </section>
  )
}

export function RetroButton({ type = 'button', className = '', children, ...props }) {
  return (
    <button type={type} className={`atlas-retro-button ${className}`} {...props}>
      {children}
    </button>
  )
}

export function RetroInput({ label, className = '', ...props }) {
  return (
    <label className={`atlas-retro-field ${className}`}>
      <span className="atlas-retro-field-label">{label}</span>
      <input className="atlas-retro-input" type="text" {...props} />
    </label>
  )
}

/**
 * Interactive proof that the palette drives genuine HTML controls, not a
 * flat preview image. No network calls, execution, or mutable application
 * state: the submit action only echoes a bounded local sample label.
 */
export function RetroWorkbench() {
  const { themeDefinition } = useTheme()
  const [sample, setSample] = useState('')
  const [lastRun, setLastRun] = useState('Ready')

  const execute = (event) => {
    event.preventDefault()
    const text = sample.trim()
    setLastRun(text ? `Preview: ${text}` : 'Enter a sample label to preview')
  }

  return (
    <RetroWindow title="Theme workbench" footer={`Atlas / ${themeDefinition.name} / local demo`}>
      <p className="atlas-retro-workbench-help">
        Test actual window, input, and button chrome. No commands are executed.
      </p>
      <form className="atlas-retro-form" onSubmit={execute}>
        <RetroInput
          label="Sample label"
          value={sample}
          maxLength={80}
          onChange={event => setSample(event.target.value)}
          placeholder="HELLO ATLAS"
        />
        <RetroButton type="submit">Preview label</RetroButton>
      </form>
      <output className="atlas-retro-output" role="status">{lastRun}</output>
    </RetroWindow>
  )
}
