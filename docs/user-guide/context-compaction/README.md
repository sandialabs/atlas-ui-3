# Context Usage and Compaction

Last updated: 2026-11-01

Every model can only read so much at once (its *context window*). In a long
conversation, or a long agent run, Atlas keeps you informed of how full that
window is and can **compact** older parts of the conversation into a summary
so the work can continue. Your full transcript is always kept.

## The context meter

On the control row under the message box, with the model and prompt, you
will see something like `Context ~82K / 128K`: roughly 82 thousand tokens
will be sent with your next message, out of a 128 thousand token window.
It is an estimate. A `?` after the window means Atlas does not know this
model's exact window and is assuming one. On a narrow screen the meter
shortens to the gauge and the estimate, for example `~82K`, so it stays on
that row; the window and the level bar return as the row gets wider.

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

### If a compaction fails

The model writing the checkpoint may come back with no summary at all -- for
example a reasoning model that spends its whole reply budget on reasoning.
Atlas retries the summary once and, if that also comes back blank, shows a
notice saying which kind of failure it was and keeps your conversation exactly
as it was. Nothing is lost or rewritten; the conversation simply continues
uncompacted, and you can try **Compact** again or send a shorter message.
