<!--
This file is used as the body of the draft release PR opened by
.github/workflows/release-cut.yml. Keep it actionable and
review-friendly. Full process docs live at
docs/developer/release-process.md.
-->

# Release {{VERSION}} ({{YEAR_MONTH}})

This PR was opened by the `release-cut` automation. It cuts
`release/{{YEAR_MONTH}}` from `main` and bumps the version. Nothing
publishes until a maintainer manually tags `v{{VERSION}}` on this
branch — see the runbook at
[docs/developer/release-process.md](../docs/developer/release-process.md).

## What this PR contains

- New branch `release/{{YEAR_MONTH}}` cut from `main` at
  `{{COMMIT_SHA}}`.
- `atlas/version.py` and `pyproject.toml` bumped from
  `{{OLD_VERSION}}` → `{{VERSION}}`.
- `CHANGELOG.md`: `## [Unreleased]` section finalized as
  `## [{{VERSION}}] - {{RELEASE_DATE}}`; new empty `[Unreleased]`
  prepended.

## Release captain

Assign yourself to this PR. You own the checklist below, the smoke
test, the tag push, and the GitHub Release.

## Stabilization window

This PR stays open for the rest of the month. During that window:

- Only bug fixes merge to `release/{{YEAR_MONTH}}`. Features and
  refactors continue to land on `main` as normal.
- Fix on `main` first, then cherry-pick to the release branch:
  `git cherry-pick -x <sha>`.
- Each cherry-pick gets a CHANGELOG note under the
  `## [{{VERSION}}]` section on the release branch.

## Pre-tag checklist

Complete every item before pushing the tag. Do not merge this PR
until the tag is pushed and publish workflows are green.

### Code & CI

- [ ] `CI/CD Pipeline` is green on the latest commit of
      `release/{{YEAR_MONTH}}`.
- [ ] `Security Checks` is green.
- [ ] No known P0 or P1 bugs filed against the release branch.
- [ ] `atlas/version.py` and `pyproject.toml` both show `{{VERSION}}`.
- [ ] `CHANGELOG.md` has a populated `## [{{VERSION}}]` section with
      at least one entry per user-visible change since the previous
      release.

### Smoke test (manual)

See [docs/developer/release-process.md#smoke-test](../docs/developer/release-process.md#smoke-test).

- [ ] `uv build --wheel` succeeds on `release/{{YEAR_MONTH}}`.
- [ ] Fresh `atlas-init` + `atlas-chat` one-shot call works against
      a real LLM.
- [ ] Tool-call path works (`atlas-chat --tools calculator_evaluate
      --tool-choice-required`).
- [ ] `atlas-server` boots and `/api/health` returns `{{VERSION}}`.

### Publish

- [ ] Tag pushed: `git tag -a v{{VERSION}} -m "Atlas UI 3 v{{VERSION}}" && git push origin v{{VERSION}}`.
- [ ] GitHub Release created from the tag, targeting
      `release/{{YEAR_MONTH}}`, with CHANGELOG section as the body.
- [ ] `pypi-publish.yml` completed, `atlas-chat=={{VERSION}}`
      visible on PyPI.
- [ ] `quay-publish.yml` completed, `quay.io/<ns>/atlas-ui-3:{{VERSION}}`
      image pushed.

### Post-publish

- [ ] This PR merged into `main` (fast-forward if possible) so the
      version bump and any hotfixes carry forward.
- [ ] Follow-up issue filed for any deferred items surfaced during
      stabilization.

## Rollback

If the release is broken in the wild, **do not rewrite history**.
Yank the bad version on PyPI and cut a patch release via the hotfix
flow in the runbook. Rolling forward beats rolling back.
