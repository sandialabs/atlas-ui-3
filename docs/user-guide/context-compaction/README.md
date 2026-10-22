# Context Usage and Compaction

Last updated: 2026-10-21

Every model can only read so much at once (its *context window*). In a long
conversation, or a long agent run, Atlas keeps you informed of how full that
window is and can **compact** older parts of the conversation into a summary
so the work can continue. Your full transcript is always kept.

## The context meter

Next to the message box you will see something like `Context ~82K / 128K`:
roughly 82 thousand tokens will be sent with your next message, out of a
128 thousand token window. It is an estimate. A `?` after the window means
Atlas does not know this model's exact window and is assuming one.

Click the meter for details: the estimate, the window, the point at which
automatic compaction starts, the size of the last request as reported by the
model provider, and the tokens used so far in this session.

## Compacting

- **Compact now:** open the meter, optionally say what to keep in most detail
  ("the database migration decisions"), and press **Compact**. Or type
  `/compact` or `/compact <focus>` in the message box. Compaction runs between
  messages; it is not available while a reply is being written.
- **Automatically:** with *Compact automatically in this conversation* ticked
  (the default), Atlas compacts on its own when the conversation gets close to
  the limit -- also in the middle of an agent run, which then carries on by
  itself without you having to send another message.

Each compaction appears in the conversation as a divider such as
`Context compacted (manual): ~95K -> ~12K tokens`. Use **Show checkpoint** to
read the summary the model now works from. Everything above the divider is
still in your transcript and in your saved conversation.
