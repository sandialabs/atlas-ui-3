import { useEffect } from 'react'

/**
 * Publishes the live height of the chat composer footer as the CSS custom
 * property `--atlas-composer-height` on <html>.
 *
 * The composer is not a fixed-height strip: it grows with the textarea (up to
 * COMPOSER_MAX_HEIGHT), with attached-file pills, and with the disconnected /
 * no-tools warning banners. Anything that floats above it -- today the feedback
 * button -- has to track that height instead of guessing a constant offset,
 * otherwise it ends up sitting on top of the Send button once the user types a
 * long message.
 *
 * Returns nothing; consumers read the variable from CSS.
 */
export const COMPOSER_HEIGHT_VAR = '--atlas-composer-height'

export function useComposerHeightVar(ref) {
  useEffect(() => {
    const el = ref?.current
    const root = typeof document !== 'undefined' ? document.documentElement : null
    if (!el || !root) return

    const publish = () => {
      const height = el.getBoundingClientRect?.().height ?? el.offsetHeight ?? 0
      root.style.setProperty(COMPOSER_HEIGHT_VAR, `${Math.round(height)}px`)
    }

    publish()

    // ResizeObserver is absent in jsdom and in a few locked-down browsers; the
    // one-shot measurement above is still correct for a static composer.
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(publish)
    observer?.observe(el)
    return () => {
      observer?.disconnect()
      // Always clear, observer or not: a stale height would leave anything
      // anchored to it floating at the wrong offset.
      root.style.removeProperty(COMPOSER_HEIGHT_VAR)
    }
  }, [ref])
}

export default useComposerHeightVar
