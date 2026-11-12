/**
 * Per-theme lazy retro CSS (issue #1119): the loader, its ThemeContext
 * integration. The build-time split and Vite plugin are covered in
 * retro-theme-css-split.test.js (Node environment, for esbuild).
 */
import { act, render } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ThemeProvider, useTheme } from '../contexts/ThemeContext'
import {
  RETRO_CSS_ATTRIBUTE, RETRO_CSS_TIMEOUT_MS, __resetRetroThemeCss, loadRetroThemeCss,
  retroThemeCssStatus, retroThemeCssUrl,
} from '../themes/retroThemeCss'
import { THEME_REGISTRY } from '../themes/themeRegistry'

const retroIds = THEME_REGISTRY.filter(theme => theme.reference).map(theme => theme.id)
const links = () => [...document.head.querySelectorAll(`link[${RETRO_CSS_ATTRIBUTE}]`)]
const linkFor = id => links().find(link => link.getAttribute(RETRO_CSS_ATTRIBUTE) === id)
const settle = async (id, event) => {
  await act(async () => {
    links().filter(link => link.getAttribute(RETRO_CSS_ATTRIBUTE) === id).at(-1).dispatchEvent(new Event(event))
    await Promise.resolve()
  })
}

let api
function Probe() {
  const theme = useTheme()
  useEffect(() => { api = theme })
  return null
}
const mount = () => render(<ThemeProvider><Probe /></ThemeProvider>)
const applied = () => document.documentElement.getAttribute('data-atlas-theme')

beforeEach(() => {
  localStorage.clear()
  links().forEach(link => link.remove())
  document.documentElement.removeAttribute('data-atlas-theme')
  __resetRetroThemeCss('v1')
})
afterEach(() => {
  vi.useRealTimers()
  __resetRetroThemeCss()
})

describe('retro theme CSS loader', () => {
  it('loads a theme stylesheet on select and applies the theme once it is in', async () => {
    mount()
    expect(applied()).toBe('dark')
    expect(links()).toHaveLength(0)
    act(() => api.setTheme('windows-xp-luna'))
    const link = linkFor('windows-xp-luna')
    expect(link.getAttribute('href')).toBe('/assets/retro/windows-xp-luna.css?v=v1')
    expect(link.rel).toBe('stylesheet')
    // The previous theme stays until the sheet loads: no unstyled retro paint.
    expect(applied()).toBe('dark')
    expect(api.selectedTheme).toBe('windows-xp-luna')
    await settle('windows-xp-luna', 'load')
    expect(applied()).toBe('windows-xp-luna')
    expect(document.documentElement.getAttribute('data-theme')).toBe('light')
    expect(localStorage.getItem('atlas-theme')).toBe('windows-xp-luna')
  })

  it('caches: one <link> and one promise per theme, reused on every switch', async () => {
    mount()
    act(() => api.setTheme('mac7'))
    await settle('mac7', 'load')
    act(() => api.setTheme('dark'))
    expect(applied()).toBe('dark')
    act(() => api.setTheme('mac7'))
    // Already loaded: applied synchronously, no new request.
    expect(applied()).toBe('mac7')
    expect(links()).toHaveLength(1)
    expect(loadRetroThemeCss('mac7')).toBe(loadRetroThemeCss('mac7'))
    expect(await loadRetroThemeCss('mac7')).toBe(true)
  })

  it('falls back to the light/dark base when the stylesheet fails, and retries on re-select', async () => {
    mount()
    act(() => api.setTheme('crt-phosphor'))
    await settle('crt-phosphor', 'error')
    expect(retroThemeCssStatus('crt-phosphor')).toBe('failed')
    expect(applied()).toBe('dark')
    expect(api.theme).toBe('dark')
    expect(linkFor('crt-phosphor')).toBeUndefined()
    // The choice is kept so a later load (or reload) restores it.
    expect(api.selectedTheme).toBe('crt-phosphor')
    expect(localStorage.getItem('atlas-theme')).toBe('crt-phosphor')
    act(() => api.setTheme('crt-phosphor'))
    expect(linkFor('crt-phosphor')).toBeDefined()
    await settle('crt-phosphor', 'load')
    expect(applied()).toBe('crt-phosphor')
  })

  it('a light theme that fails falls back to Light', async () => {
    mount()
    act(() => api.setTheme('beos'))
    await settle('beos', 'error')
    expect(applied()).toBe('light')
  })

  it('a stylesheet that never answers times out into the fallback', async () => {
    vi.useFakeTimers()
    const result = loadRetroThemeCss('teletext')
    await vi.advanceTimersByTimeAsync(RETRO_CSS_TIMEOUT_MS)
    expect(await result).toBe(false)
    expect(retroThemeCssStatus('teletext')).toBe('failed')
  })

  it('adopts the <link> the first-paint bootstrap wrote for the saved theme', async () => {
    localStorage.setItem('atlas-theme', 'amiga-workbench')
    const link = document.createElement('link')
    link.rel = 'stylesheet'
    link.href = retroThemeCssUrl('amiga-workbench')
    link.setAttribute(RETRO_CSS_ATTRIBUTE, 'amiga-workbench')
    document.head.appendChild(link)
    mount()
    // The bootstrap already painted it; React keeps it while confirming.
    expect(applied()).toBe('amiga-workbench')
    await settle('amiga-workbench', 'load')
    expect(links()).toHaveLength(1)
    expect(retroThemeCssStatus('amiga-workbench')).toBe('loaded')
    expect(applied()).toBe('amiga-workbench')
  })

  it('a failed first-paint stylesheet falls back after React starts', async () => {
    localStorage.setItem('atlas-theme', 'vaporwave')
    const link = document.createElement('link')
    link.setAttribute(RETRO_CSS_ATTRIBUTE, 'vaporwave')
    document.head.appendChild(link)
    mount()
    await settle('vaporwave', 'error')
    expect(applied()).toBe('dark')
  })

  it('an already-settled first-paint sheet is checked without waiting for events', async () => {
    const bootstrapLink = (id, rules) => {
      const link = document.createElement('link')
      link.setAttribute(RETRO_CSS_ATTRIBUTE, id)
      Object.defineProperty(link, 'sheet', { value: { cssRules: rules } })
      document.head.appendChild(link)
    }
    bootstrapLink('ibm-3270', [{}])
    bootstrapLink('templeos', []) // What Chrome leaves behind a 404.
    expect(await loadRetroThemeCss('ibm-3270')).toBe(true)
    expect(await loadRetroThemeCss('templeos')).toBe(false)
    expect(linkFor('templeos')).toBeUndefined()
  })

  it('re-loads a first-paint sheet at the end of <head> when app styles follow it (dev server)', async () => {
    localStorage.setItem('atlas-theme', 'nextstep')
    const early = document.createElement('link')
    early.setAttribute(RETRO_CSS_ATTRIBUTE, 'nextstep')
    document.head.appendChild(early)
    const appStyle = document.createElement('style')
    document.head.appendChild(appStyle)
    try {
      mount()
      expect(applied()).toBe('nextstep')
      const late = links().find(link => link !== early)
      expect(late.previousElementSibling).toBe(appStyle)
      expect(early.isConnected).toBe(true) // Still styling the page meanwhile.
      await settle('nextstep', 'load')
      expect(links()).toEqual([late])
      expect(applied()).toBe('nextstep')
    } finally {
      appStyle.remove()
    }
  })

  it('Light and Dark never load anything', async () => {
    mount()
    act(() => api.setTheme('light'))
    expect(applied()).toBe('light')
    expect(await loadRetroThemeCss('dark')).toBe(true)
    expect(links()).toHaveLength(0)
  })

  it('a build without a CSS version (flag off, or tests) loads nothing and applies directly', async () => {
    __resetRetroThemeCss('')
    mount()
    act(() => api.setTheme('win95'))
    expect(applied()).toBe('win95')
    expect(await loadRetroThemeCss('win95')).toBe(true)
    expect(links()).toHaveLength(0)
  })

  it('a build with VITE_FEATURE_RETRO_THEMES=false loads nothing even with a version', async () => {
    vi.resetModules()
    vi.doMock('@atlas/retro-themes', () => import('../themes/retro/retroThemes.disabled'))
    try {
      const loader = await import('../themes/retroThemeCss')
      loader.__resetRetroThemeCss('v1')
      expect(loader.retroThemeCssEnabled()).toBe(false)
      for (const id of retroIds) {
        expect(loader.needsRetroThemeCss(id)).toBe(false)
        expect(await loader.loadRetroThemeCss(id)).toBe(true)
      }
      expect(links()).toHaveLength(0)
    } finally {
      vi.doUnmock('@atlas/retro-themes')
      vi.resetModules()
    }
  })
})
