# Canvas Layout Controls

Last updated: 2026-09-02

The canvas is the panel that shows tool output -- documents, images, charts,
rendered HTML. By default it opens beside the chat and takes about half the
window. On a narrow window, and especially on a phone, a side-by-side split
leaves both the chat and the document too narrow to read. The canvas header
carries two controls for reshaping that split, plus the existing close button.

## The controls

All three live in the top-right corner of the canvas header.

| Control | What it does |
| --- | --- |
| Expand / Shrink (`Maximize` / `Minimize` icon) | Toggles between **half** (the canvas shares the window with the chat) and **full** (the canvas takes the whole content area and the chat is hidden). |
| Move (`PanelTop` / `PanelRight` icon) | Toggles between **beside the chat** (canvas on the right) and **above the chat** (canvas stacked on top, chat below). |
| Close (`X`) | Hides the canvas entirely. |

Hiding the canvas is not permanent: reopen it from the canvas button in the app
header, and new canvas content arriving from a tool reopens it automatically.

## Narrow windows and phones

Below 768px wide the layout is always stacked -- canvas above, chat below --
because a horizontal split at that width is unusable. The Move control is
hidden while that applies, since there is nothing to choose. Your stored
"beside the chat" preference is not overwritten: widen the window (or rotate a
tablet back to landscape) and the side-by-side layout returns.

The Expand control still works on a narrow window, and is the fastest way to
read a document on a phone: expand to full, read, then shrink back to get the
chat input in view again.

## Persistence

Both choices are remembered in the browser's local storage
(`chatui-canvas-size` and `chatui-canvas-orientation`), so the same device keeps
your layout across sessions. The setting is per-browser, not per-account -- your
phone and your desktop each keep their own preference.
