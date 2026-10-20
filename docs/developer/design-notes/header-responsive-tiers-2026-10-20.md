# Header Responsive Tiers

Date: 2026-10-20

Why the top bar's shapes are decided by measuring the bar rather than by a
fixed width, and how the security level stays visible while the bar collapses
(issue #1071).

## The bug

With compliance levels enabled, the top bar's trailing buttons ran off the
right edge of the page at intermediate widths and became unreachable.

Measured in the browser at a 1400px viewport (200px conversations sidebar, so
the bar is 1200px wide), the File Manager button ended at x=1421 and the Canvas
button at x=1465 -- both past the right edge of a 1400px window. The bar's
right-hand padding edge was at x=1384, so 81px of the bar was off-screen.

The same sweep, with compliance levels disabled, showed no spill at any
width. The difference is the compliance (security level) selector, which
joins the conversation setup group on the left of the bar and makes it about
150px wider.

## Cause

The bar switched to its overflow menu at a fixed width,
`DESKTOP_ACTIONS_MIN_WIDTH = 1160`, measured on the bar itself. That number
had been picked before the security level selector joined the setup group, and
nothing re-derived it when the group's contents changed. The bar therefore
showed the full set of controls at widths where they did not fit: the left
group could not shrink (it holds fixed-size controls with nothing to
truncate), so the right-hand group was pushed past the edge instead.

Two smaller inconsistencies came with it:

- The connection status text followed the viewport (`hidden sm:inline`), so a
  wide sidebar on a wide screen left the bar with a status label it had no
  room for.
- The workspace name followed the viewport too (`hidden lg:inline`), so the
  name appeared in bars that had no room for it and disappeared from bars
  that did.

And the security level itself only existed in the bar as part of the full
setup group, so it vanished from the bar for every width below the collapse
point -- a wide band of laptop and tablet widths -- even though it was the
one control a user most needs to see.

## Fix

The bar has three shapes, widest first:

| Shape     | Left of the bar                                    | Right of the bar                     |
| --------- | -------------------------------------------------- | ------------------------------------ |
| `full`    | setup group, Sources, Workspace                    | status, user, every action button    |
| `compact` | setup group, Sources, Workspace                    | status, menu button                  |
| `minimal` | security level (compact), New Chat, Sources, Workspace | status, menu button             |

The action buttons move into the overflow menu first, then the save mode and
the setup help, and the security level is the last control to leave the bar:
it stays in the bar down to `SECURITY_LEVEL_MIN_WIDTH` in a narrow form, and
after that it is in the overflow menu, where it was all along.

### The full shape is measured, not guessed

The bar measures three things and adds them up:

- the left group (`useElementWidth` on the left-hand section),
- the connection status,
- the row of action buttons.

If they do not fit in the bar's own width, the bar does not show them. The
threshold therefore follows the deployment: with the compliance selector
enabled the bar waits for about 1300px of its own width, and without it the
bar fills up at about 1160px. A long save-mode label, a long workspace name
or a deployment without the agent-mode toggle all move the threshold with
them.

Two details make that measurement trustworthy:

- The action buttons are measured in their own `w-max` wrapper, so their
  width is the same whether the bar is showing them or measuring them. A
  cluster that had been squeezed would report the squeezed width, and the bar
  would then believe it fit.
- The collapsed cluster stays in the DOM, out of flow (`absolute invisible`),
  so it can be measured while it is hidden. `visibility: hidden` also takes
  it out of the accessibility tree and the tab order, so the collapsed
  buttons are neither announced nor focusable.

### What gives way, and what does not

The user name is the one piece of unbounded content in the bar, so it is the
one that gives way: it truncates, keeps its full address in its `title`, and
the bar reserves only a minimum width for it in the fit test. The buttons
around it never shrink (`flex-shrink-0`), so a long address can no longer
push a control off the page -- the earlier failure mode.

The other unbounded text is bounded instead: the source count is capped
(`max-w-[5rem]`), the workspace name is capped (`max-w-[8rem]`), and the
connection status is capped (`max-w-[5rem]`), because a status can carry a
whole sentence ("Unauthenticated: ...").

### The remaining thresholds are budgets

The two lower thresholds are constants, because their content is small and
known:

- `COMPACT_ACTIONS_MIN_WIDTH` (1160) is the narrowest bar that still holds
  the widest setup group beside the menu button.
- `ACTION_LABELS_MIN_WIDTH` (800) is where the left-hand buttons drop their
  text labels.
- `SECURITY_LEVEL_MIN_WIDTH` (420) is where the security level leaves the bar
  for the overflow menu.
- `STATUS_TEXT_MIN_WIDTH` (560) is where the connection status shows its
  label rather than only its dot.

Both were checked against the rendered bar with the widest content the
controls allow (the longest save-mode label, a capped workspace name and
source count, the widest status text).

### The overflow menu hangs off the bar

The bar's height is not fixed: the security level control is a labelled box
(54px) and the icon buttons are 36px, so the bar is 53px, 69px or 87px tall
depending on its shape and the padding tier. The overflow menu used to be
positioned with a hard-coded offset (`top-[57px] sm:top-[65px]`), which
covered the bottom of the bar's own controls once the bar grew taller.

The bar's height is measured alongside its width (`useElementSize`), and the
menu panel is placed at the bar's bottom edge, with its maximum height derived
from it. The measurement also removed the 4px overlap the old offset had in
the narrow shapes.

## Verification

Measured in the browser with Playwright, sweeping the viewport from 320px to
1920px, with compliance levels on and off, checking that no control is
painted past the bar's right padding edge:

- Before: up to 121px of spill at 1160px of bar width, with compliance
  levels on; none with them off.
- After: zero spill at every width, in both configurations, and the bar
  never grows a second row.

`src/test/header-responsive-tiers.test.jsx` pins the behaviour: what is in the
bar at each width, that the security level outlives the labels, and that the
action buttons move into the menu one pixel before the bar runs out of room.
`src/test/header-container-width.test.jsx` pins the hook's contract and the
thresholds.
