# Browsing Older Saved Conversations (issue #1085)

The conversation list in the left sidebar shows the newest saved
conversations first. When you have more history than the first page holds,
a **Load more** control appears at the bottom of the list: press it and the
next page of older conversations is fetched and added to the list. Press it
again to go further back, and keep pressing it until the whole history is
on screen.

(Last updated: 2026-10-20.)

## What the control does

- **It appends.** The conversations already on screen stay where they are.
  The older ones join them at the bottom, so the row you were reading does
  not move and the conversation you have open stays open.
- **It pages.** Only the next page is fetched, not the whole history, so a
  long history does not have to load all at once.
- **It says what is going on.** While a page is on its way the control
  reads *Loading older conversations...*. If the fetch fails, the reason is
  shown with a **Retry** button, and the conversations already on screen
  stay untouched.
- **It knows when it is done.** Once every saved conversation has been
  loaded the control is replaced by *All conversations loaded*.

The list is ordered newest to oldest, and the same order is used for every
page, so walking back through the history reads as one continuous list.

## What you can still do while browsing

Everything the list offers works the same way on an older page: opening a
conversation, renaming it, tagging it, and deleting it. Deleting a
conversation shortens the history, and the next page accounts for that, so
no conversation is skipped.

A refresh -- a message saved, a background run finished -- keeps the pages
you have already loaded. The list is not sent back to the top of the
history while you are reading it.

## Rules the server enforces

Every page is fetched from the server with the same rules as the first one:

- Only your own conversations are returned. Another account's conversations
  never appear on any page.
- A tag filter applies to every page, and the count of what is left follows
  the filter.
- A conversation recorded under a different security level still cannot be
  continued under the current one, whether it is on the first page or an
  older one. The sidebar marks the row; the server refuses the conversation
  when it is opened.

## Browser-stored conversations

Conversations saved in the browser (rather than on the server) are paged the
same way, from the browser's own storage. The two histories are separate:
the **Load more** control belongs to whichever one is open.
