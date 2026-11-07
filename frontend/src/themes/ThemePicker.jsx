import { useState } from 'react'
import { useTheme } from '../contexts/ThemeContext'
import { THEME_REGISTRY } from './themeRegistry'
import { RetroWorkbench, RetroInput, RetroButton } from './RetroControls'

/** Native radio controls preserve arrow-key navigation and screen-reader state. */
export default function ThemePicker() {
  const { theme, setTheme } = useTheme()
  const [filter, setFilter] = useState('')
  const [category, setCategory] = useState('all')
  const query = filter.trim().toLowerCase()
  const categories = Array.from(new Set(THEME_REGISTRY.map(entry => entry.era.split(' · ')[0]))).sort()
  const visibleThemes = THEME_REGISTRY.filter(entry =>
    (category === 'all' || entry.era.split(' · ')[0] === category) &&
    (!query || [entry.name, entry.era, entry.description, ...(entry.tags || [])]
      .join(' ').toLowerCase().includes(query)),
  )
  const resetFilter = () => { setFilter(''); setCategory('all') }

  return (
    <fieldset className="space-y-3" aria-label="Registered interface themes">
      <legend className="text-sm font-medium text-gray-100">Theme gallery</legend>
      <div className="atlas-theme-filter flex flex-wrap items-end gap-2">
        <RetroInput
          label="Find a theme"
          type="search"
          maxLength={80}
          value={filter}
          onChange={event => setFilter(event.target.value)}
          placeholder="Search designs, tags, and eras"
          autoComplete="off"
        />
        <label className="atlas-theme-category">
          <span>Design category</span>
          <select value={category} onChange={event => setCategory(event.target.value)}>
            <option value="all">All categories</option>
            {categories.map(era => <option value={era} key={era}>{era}</option>)}
          </select>
        </label>
        {(filter || category !== 'all') && (
          <RetroButton onClick={resetFilter}>Clear theme filter</RetroButton>
        )}
      </div>
      <p className="text-xs text-gray-400" aria-live="polite">
        Showing {visibleThemes.length} of {THEME_REGISTRY.length} themes
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Interface theme">
        {visibleThemes.map(entry => (
          <label
            key={entry.id}
            className="atlas-theme-option atlas-theme-chrome flex cursor-pointer items-center gap-3 rounded-lg border border-gray-600 bg-gray-800 p-3 text-gray-100"
          >
            <input
              type="radio"
              name="atlas-interface-theme"
              value={entry.id}
              checked={theme === entry.id}
              onChange={() => setTheme(entry.id)}
              aria-label={entry.name}
              className="atlas-theme-radio shrink-0"
            />
            <span
              className="atlas-theme-preview shrink-0"
              data-preview={entry.id}
              aria-hidden="true"
              style={{
                '--preview-bg': entry.preview.background,
                '--preview-surface': entry.preview.surface,
                '--preview-fg': entry.preview.foreground,
                '--preview-accent': entry.preview.accent,
              }}
            >
              <span className="atlas-theme-preview-bar" />
              <span className="atlas-theme-preview-line" />
              <span className="atlas-theme-preview-line atlas-theme-preview-line-short" />
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-semibold">{entry.name}</span>
              <span className="block text-xs text-gray-300">{entry.era}</span>
              <span className="block text-xs text-gray-400">{entry.description}</span>
            </span>
          </label>
        ))}
        {visibleThemes.length === 0 && (
          <p className="text-sm text-gray-300">
            No matching themes. Clear the filter to see all available styles.
          </p>
        )}
      </div>
      <p className="text-xs text-gray-400">
        Retro palettes from{' '}
        <a
          href="https://github.com/NovusGFX/retro-design-system"
          target="_blank"
          rel="noopener noreferrer"
          className="atlas-theme-source-link underline"
        >
          NovusGFX Retro Design System
        </a>{' '}
        (MIT). Themes are bundled by Atlas developers, not installed by users.
      </p>
      <details className="atlas-retro-workbench-details">
        <summary>Try live retro controls</summary>
        <RetroWorkbench />
      </details>
    </fieldset>
  )
}
