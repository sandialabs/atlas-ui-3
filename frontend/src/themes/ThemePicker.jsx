import { useTheme } from '../contexts/ThemeContext'
import { THEME_REGISTRY } from './themeRegistry'

/** Native radio controls preserve arrow-key navigation and screen-reader state. */
export default function ThemePicker() {
  const { theme, setTheme } = useTheme()

  return (
    <fieldset className="space-y-3" aria-label="Registered interface themes">
      <legend className="text-sm font-medium text-gray-100">Theme gallery</legend>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Interface theme">
        {THEME_REGISTRY.map(entry => (
          <label
            key={entry.id}
            className="atlas-theme-option atlas-theme-chrome flex cursor-pointer items-center gap-3 rounded-lg border border-gray-600 bg-gray-800 p-3 text-gray-100 focus-within:ring-2 focus-within:ring-blue-400"
          >
            <input
              type="radio"
              name="atlas-interface-theme"
              value={entry.id}
              checked={theme === entry.id}
              onChange={() => setTheme(entry.id)}
              aria-label={entry.name}
              className="shrink-0 accent-blue-500"
            />
            <span
              className="atlas-theme-preview shrink-0"
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
      </div>
    </fieldset>
  )
}
