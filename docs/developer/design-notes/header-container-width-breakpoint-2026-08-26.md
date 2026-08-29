# Header Container-Width Breakpoint

Date: 2026-08-26

Why the header's desktop button cluster is gated on a measured element width
instead of a CSS media query.

## The bug

At a 1280px viewport with the conversations sidebar open, the header's controls
overlapped and ran off the right edge of the bar:

- The trailing four buttons (Agent Portal, Tools, File Manager, Canvas) were
  laid out from x=1279 to x=1491 — entirely past the header's right edge at
  x=1280, so they were unreachable.
- The model selector (right edge 754) and the save-mode button (left edge 752)
  painted over each other.
- The header stood 89px tall instead of 69px, because the "New Chat" label had
  wrapped onto a second line.

## Cause

The desktop cluster was gated on `min-[1280px]:flex`, and the hamburger that
replaces it on `min-[1280px]:hidden`. Those are **viewport** media queries, but
the header is not the viewport: `App.jsx` lays it out inside
`flex flex-col flex-1 min-w-0`, next to a 256px sidebar. At a 1280px viewport
the header is therefore only 1024px wide.

So the cluster switched on a full sidebar-width before there was room for it.
The bar needs roughly 1289px of its *own* width to hold every control; it was
being told to render them at 1024px. Flexbox had nothing left to give — the
fixed-size icon buttons cannot shrink and the left section is deliberately not
`min-w-0` — so the surplus became overflow and collision rather than a graceful
squeeze.

The two-line "New Chat" was a second, independent defect: the label span had no
`whitespace-nowrap`, so under flex pressure the text wrapped instead of holding
the button's min-content width. That also invalidated the mobile menu panel's
hardcoded `top-[57px] sm:top-[65px]` offset, which assumes a single-row header.

## Fix

`useElementWidth` (a small `ResizeObserver` hook) measures the header itself,
and `DESKTOP_ACTIONS_MIN_WIDTH = 1320` gates the cluster on that width. The
threshold carries headroom over the measured 1289px fit point for fonts and
locale-dependent label widths.

Three supporting changes:

- The username in the cluster is `truncate max-w-[12rem]` with a `title`. It is
  the one piece of unbounded content in the bar; without a bound, a long address
  could push its neighbours and recreate the collision above any threshold.
- The three wrappable labels ("Sources", "New Chat", the save-mode label) are
  `whitespace-nowrap`, keeping the header one row tall.
- The compact menu's overlay is gated on the render itself
  (`mobileMenuOpen && !showDesktopActions`). The panel and backdrop used to be
  hidden by the same CSS breakpoint that hid the hamburger; now that both are
  driven by measured width, an open menu would otherwise be stranded on screen
  with no button to dismiss it. Gating on an effect alone is not enough --
  effects run after paint, so the backdrop would cover the desktop cluster for a
  frame, long enough to swallow a click. Overlay and hamburger read the same
  state and flip in the same commit. An effect still resets `mobileMenuOpen`, so
  the menu does not spring back open if the header narrows again.

## Why not a CSS container query

`@container` is the natural tool for this, and it was the first choice. It does
not work here: `container-type: inline-size` applies **layout containment**,
which makes the element a containing block for its `position: fixed`
descendants. The header contains three of them — the mobile-menu backdrop
(`fixed inset-0`), the menu panel, and the API key modal — so containing the
header would have re-anchored all three to the header's box instead of the
viewport, trading one layout bug for three. Measuring in JS leaves them
positioned against the viewport, as they were.

Tailwind v3.4 also has no container-query support without the
`@tailwindcss/container-queries` plugin, which is not a dependency of this
project.

## Verification

Measured in-browser with Playwright, sweeping the viewport from 320px to 2560px
with the sidebar both open and closed, checking every visible header control
pair for bounding-box intersection and checking for content past the header's
right edge. Before the fix, 1280px/sidebar-open reported a 2px overlap and 211px
of spill. After, every width reports zero overlap and zero spill, and the header
stays one row tall (53/69/71px across the padding tiers).

`src/test/header-container-width.test.jsx` pins the hook's contract: it measures
before paint (so a wide header never flashes its compact layout), it tracks
resizes, it rounds sub-pixel widths, it still measures once when
`ResizeObserver` is unavailable, and it disconnects on unmount.
