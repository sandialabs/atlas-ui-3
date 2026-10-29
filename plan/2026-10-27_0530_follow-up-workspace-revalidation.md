# Plan: Revalidate workspace access before atlas_follow_up (#1079)

Issue: https://github.com/sandialabs/atlas-ui-3/issues/1079
Branch: `fix/1079-follow-up-workspace-revalidation`

## Problem

`atlas_follow_up` (added in #1078) continues a sub-conversation on the launch
spec recorded on its `RunRecord`: model, tools, data sources, workspace id/name,
compliance level. Before admitting the follow-up turn it re-authorizes the
model (`resolve_model`), the tools (`resolve_child_tools` via
`_start_child_run`) and the data sources (`resolve_child_data_sources`), and it
refuses a compliance-level change -- but it never resolves the saved
`workspace_id` against the caller's access *now*. `ChatService` treats
`workspace_id` as purely advisory (`atlas/application/chat/service.py` stores it
in session context and conversation metadata; it is documented as "never used
for authorization"), so nothing downstream rejects a follow-up whose workspace
was deleted or reassigned between launch and follow-up. The saved workspace
would flow unchecked into the child turn and the conversation metadata.

## The existing authorization check to reuse

`WorkspaceRepository.get_workspace(workspace_id, user_email)`
(`atlas/modules/chat_history/workspace_repository.py`) is the repo-level
ownership/visibility check the launch path already relies on:
`resolve_workspace` (launcher.py) resolves by id first, scoped to
`user_email`, so a deleted workspace, a workspace reassigned to someone else,
and another user's workspace all come back "not found" for this caller. The
follow-up should reuse exactly that semantics -- lookup scoped to the caller --
without the name fallback, because the follow-up must never *select* a
workspace.

## Fix

In `atlas/application/chat/runs/follow_up.py`, before admitting the run
(alongside `resolve_model`, after the compliance check):

1. Take the repository from the factory; if workspaces are not configured,
   refuse (fail closed), mirroring the launch path's refusal.
2. Resolve the saved workspace **by id only**:
   - a spec without a `workspace_id` (never produced by the current launch
     path) refuses: the guarantee cannot be checked, so it fails closed;
   - a repository error refuses (fail closed), logged;
   - "not found" (deleted, revoked, reassigned to another user, or the id now
     points at a workspace the caller does not own) refuses with one
     model-readable `LaunchRefused` that names the saved workspace (the caller
     already knows it: it is in the launch/follow-up handle) and says to launch
     a new sub-conversation.
3. No name fallback and no "available workspaces" listing in the refusal: the
   follow-up continues the child under its saved workspace or not at all. A
   workspace deleted and recreated under the same name (new id) therefore
   refuses -- the association the child was launched under is gone.
4. Only identity is carried forward (`workspace["id"]`, `workspace["name"]` --
   the current name, so a rename does not break a follow-up). The workspace's
   current *config* is deliberately ignored: tools and data sources still come
   from the saved, narrowed launch spec, so a tool or source added to the
   workspace since the launch cannot widen the child.
5. Everything else is untouched: model/tool/data-source re-authorization, the
   compliance-level check, the per-run child cap, atomic admission.

## Tests (`atlas/tests/test_atlas_launch_tool.py`, follow-up section)

- follow-up succeeds with an unchanged, accessible workspace and the child turn
  still carries the saved `workspace_id` (the success arm of the new check).
- refused when the workspace is deleted between launch and follow-up (and no
  chat service is built for the refused turn).
- refused when the workspace resolves for a different owner (reassigned /
  other-user), with the saved name still present (no cross-user leak beyond
  what the caller already knows).
- refused when the workspace was deleted and recreated under the same name
  (changed association; resolving must be by id, never by name).
- allowed when the workspace is renamed (id still resolves): the follow-up
  carries the current name -- this pins the boundary of "changed association".
- the workspace's config is not a widening channel: adding tools to the
  workspace after the launch does not change the follow-up's tool set.
- the refusal surfaces as a failed `ToolResult` through
  `execute_follow_up_tool`.
- continued narrowing still holds after the new check (existing tool/source
  narrowing tests must keep passing; the narrowing tests exercise the same
  admission path).

## Docs and fragments

- `docs/developer/design-notes/atlas-launch-sub-conversations-2026-09-13.md`:
  the follow-ups section says the spec is re-checked "every part" -- make the
  workspace part explicit (re-resolved by id, refused when gone, identity only).
- `changes/1079.fix.md`: one-sentence release note.
- PR validation script `test/pr-validation/test_pr1079_follow_up_workspace_revalidation.sh`
  with `fixtures/pr1079/`: real backend + scripted mock model; follow-up works
  while the workspace exists, then the workspace is deleted over the REST API
  and the follow-up is refused; deleting and recreating a same-named workspace
  is still refused (id-based, not name-based). Horizontal scaling: the change
  adds one stateless repository read per follow-up admission against the
  shared database; no in-process state is introduced.

## Non-goals

- No change to the launch path (it already resolves the workspace).
- No change to the chat service's advisory treatment of `workspace_id`.
- No new refusal for a renamed workspace.