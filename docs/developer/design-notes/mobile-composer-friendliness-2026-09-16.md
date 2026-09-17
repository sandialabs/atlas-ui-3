# Mobile and In-Car Composer Friendliness

Date: 2026-09-16

Two things made Atlas awkward to use one-handed on a phone, and close to
unusable from a car mount. Both are in the chat composer area.

## 1. The feedback button sat on top of Send

`FeedbackButton` was pinned at a constant offset from the bottom of the
viewport (`bottom-32`, i.e. 128px, on anything narrower than 1102px). The
composer is not a fixed-height strip: the textarea auto-resizes up to
`COMPOSER_MAX_HEIGHT`, and above it sit the active-tools row, the attached-file
pills, and the disconnected / no-tools / agent-mode warning banners. Once a
message ran past a couple of lines the composer grew past 128px and the round
feedback bubble landed squarely on the Send button.

Measured on a 390x844 viewport with an eight-sentence message, the feedback
button's 44x44 box was *entirely inside* the Send button's box -- 1936px of
overlap, with the send arrow completely hidden. A thumb aimed at Send opened
the feedback modal instead.

### Fix

`ChatArea` publishes the live composer height as the CSS custom property
`--atlas-composer-height` (see `hooks/useComposerHeightVar.js`, a
`ResizeObserver` on the composer `<footer>`), and the feedback button anchors
itself with `bottom: calc(var(--atlas-composer-height, 8rem) + 1rem)`. The
button now tracks the composer instead of guessing a constant, so it stays
clear no matter how far the textarea grows or how many banners are stacked.
The fallback keeps it clear before the first measurement lands, and the hook
clears the property on unmount so a stale height cannot strand anything
anchored to it.

Same 390x844 reproduction after the change: 0px overlap.

### The same bug, one element over

The "Powered by ATLAS" logo on the welcome screen (gated on
`VITE_FEATURE_POWERED_BY_ATLAS`) was pinned the same way, at
`bottom-32 sm:bottom-36 md:bottom-40`. Typing a long message before sending the
first one grows the composer past those offsets and the logo is drawn over it --
measured at 390x844 with the flag on, it sat 116px inside the composer footer.
It is decorative and not interactive, so it cost appearance rather than taps,
but it is the same root cause and takes the same one-line anchor. Also 0px after.

## 2. New Chat raised a native confirm on every use

`clearChat` called `window.confirm` whenever the chat had *any* content. That
is a hard two-step on a phone -- and a native dialog cannot be styled, cannot
be made touch-sized, and steals focus -- for an action users take constantly.

The confirm now fires for exactly one case: an untracked reply that is still
being generated and would be cancelled outright. That is the only genuinely
irreversible outcome. (A reply belonging to a *tracked* background run is not
affected: it keeps running and New Chat is pure navigation, per issue #884.)

Every other New Chat clears immediately and pushes a toast carrying an **Undo**
action. This required a small addition to `ToastProvider`: a toast may now
carry one inline `action: { label, onClick }`, rendered as a full-size button.

### What Undo can and cannot restore

`handle_restore_conversation` rejects any conversation id the configured
repository does not know, and for an id it *does* know it deliberately ignores
the client's message payload in favour of the stored copy -- a client must not
be able to replay forged history into the LLM context. Undo respects that
rather than working around it, so it has two shapes:

- **With a real `activeConversationId`** (server save mode), Undo goes through
  `loadSavedConversation`, which sends `restore_conversation`. The server
  reloads the canonical transcript into the session history and the next turn
  has full prior context.
- **Without one** -- incognito is the default, and there the conversation only
  ever existed in the tab -- there is nothing on the server to restore from and
  the session has already been reset. Undo puts the transcript back locally and
  appends a system row saying the assistant no longer has those messages in
  context. Undo never invents an id to paper over this: a fabricated id would
  draw an error frame from the backend and no re-seed, while the UI showed what
  looked like a successful recovery.

A better long-term answer is to *defer* `reset_session` until either the user
sends the first turn of the new chat or the undo window closes; then Undo in
incognito needs no re-seed at all because the session was never torn down.
That is a larger change to the session lifecycle and is not attempted here.

### Invalidating the offer

Undo is only valid while the chat it cleared into is still untouched. Sending a
turn, loading a conversation from history, or clearing again all retire the
offer and dismiss its toast, and the action itself re-checks a token before
running. Without that, tapping a stale Undo would reset the replacement chat --
and in incognito that exchange is saved nowhere and would be gone for good.

## 3. Touch targets

The composer's Upload, Stop and Send buttons were 44-52px wide. They are now
`min-w-[48px] min-h-[48px]` (Send and Stop-streaming at 56px wide, with a 24px
icon), and the feedback button went from 44px to 56px. 44px is the usual
minimum touch target; a moving vehicle argues for more.

## Verification

`test_e2e`-style Playwright runs against two local servers -- one serving the
pre-change bundle, one the fixed bundle -- at 390x844 and 1280x800, measuring
the two buttons' bounding boxes and driving a real New Chat. Unit coverage
lives in `frontend/src/test/composer-floating-controls.test.jsx`,
`new-chat-undo.test.jsx`, `new-chat-stops-generation.test.js` and
`toast-action.test.jsx`.
