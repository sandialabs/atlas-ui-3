# Release Process

Last updated: 2026-04-22

This document is the canonical runbook for cutting a release of Atlas UI 3.
It covers both the **monthly release cadence** and **hotfix releases**. If
you are about to publish a version, follow the checklist in
[Cutting a monthly release](#cutting-a-monthly-release) top-to-bottom.

---

## Philosophy

Atlas UI 3 follows a **monthly release cadence**. Each calendar month
produces at most one minor release; patch releases come out of a
long-lived `release/YYYY.MM` branch as needed for hotfixes.

The model is small and deliberately boring:

- **`main`** is trunk. It is always deployable. Every PR on `main` must
  pass CI and add a `CHANGELOG.md` entry under `## [Unreleased]`.
- **`release/YYYY.MM`** is cut from `main` during the last week of each
  month. It is the stabilization branch for that month's release. The
  release branch is frozen to bug fixes only until the tag is pushed.
- **`vX.Y.Z`** is the tag that ships. Pushing a `v*.*.*` tag triggers
  `pypi-publish.yml` (on GitHub Release) and `quay-publish.yml` (on tag
  push). There is no other way to publish.

Automation handles the mechanical parts of cutting the branch; humans
make the go/no-go call, run the smoke tests, and push the tag. See
[Guardrails](#guardrails) for what automation will and will not do.

### Versioning

The project uses **SemVer**: `MAJOR.MINOR.PATCH`.

| Change                                            | Bump                   |
|---------------------------------------------------|------------------------|
| Normal monthly release                            | MINOR (`0.1.5 → 0.2.0`) |
| Hotfix on an already-shipped release branch       | PATCH (`0.2.0 → 0.2.1`) |
| Breaking API or config change                     | MAJOR                  |
| Pre-1.0: still use MINOR for breaking changes     | MINOR                  |

> **Open policy question (see bottom of this file):** do we eventually
> switch to CalVer (`YYYY.MM.PATCH`)? SemVer is the current default
> until a maintainer decision is recorded here.

The version appears in exactly two places and must change atomically
in a single commit:

- `atlas/version.py` — `VERSION = "X.Y.Z"`
- `pyproject.toml` — `version = "X.Y.Z"`

`frontend/package.json` stays at `0.0.0`; the UI reads its version from
the Python package at build time via `GIT_HASH` / `APP_VERSION` build
args.

---

## Cutting a monthly release

### Timeline

| When                        | What                                                         | Who       |
|-----------------------------|--------------------------------------------------------------|-----------|
| Day 22 of month, 14:00 UTC  | `release-cut.yml` cron opens a draft release PR              | Automation |
| Day 22–27                   | Stabilization: only bug fixes merged to `release/YYYY.MM`    | Maintainers |
| Last workday of month       | Final smoke test → tag `vX.Y.Z` → publish                    | Release captain |
| Day after release           | Merge release branch back into `main` to carry the bump      | Release captain |

The automation is idempotent: if the PR already exists for the current
month, the cron is a no-op.

### Step-by-step

#### 1. Automation cuts the branch (day 22)

The `release-cut` workflow fires on the 22nd of each month. It:

1. Reads the current version from `atlas/version.py`.
2. Computes the next version (minor bump by default).
3. Creates `release/YYYY.MM` from the current tip of `main`.
4. Bumps `atlas/version.py` and `pyproject.toml` on that branch.
5. Rewrites `CHANGELOG.md`: the `## [Unreleased]` contents become
   `## [X.Y.Z] - YYYY-MM-DD`, and a fresh empty `## [Unreleased]`
   section is prepended.
6. Opens a **draft** PR titled `release: X.Y.Z (YYYY-MM)` that targets
   `main`. The PR body contains the full release checklist (below).

If you need to run this manually (e.g., out-of-cycle release or dry
run), use **Actions → Release: cut monthly branch → Run workflow**.
You can override the computed version with the `version` input, and
`dry_run: true` prints the plan without pushing anything.

#### 2. Release captain takes ownership

A maintainer claims the draft PR (self-assign) and becomes the
**release captain**. Responsibilities:

- Triage any bug-fix PRs that need to land on the release branch.
- Run the smoke tests before tagging.
- Push the tag and create the GitHub Release.
- Merge the release PR back into `main` after the tag ships.

#### 3. Stabilization window (day 22 → end of month)

Only fixes land on `release/YYYY.MM` during this window. The rule of
thumb for what qualifies:

- Crashes, data loss, security fixes, install/import failures → yes
- Small user-visible regressions that landed since the last release → yes
- New features, refactors, docs-only changes → **no**, those stay on `main`

For a bug that affects both `main` and the release branch, land the
fix on `main` first (as you normally would), then cherry-pick the
merge commit onto `release/YYYY.MM`:

```bash
git checkout release/2026.05
git cherry-pick -x <sha-on-main>
git push
```

Add the cherry-pick under the current release's CHANGELOG section on
the release branch.

#### 4. Pre-tag checklist

Before tagging, the release captain completes every item on the
checklist embedded in the release PR (see
[.github/release-checklist.md](../../.github/release-checklist.md)).
The critical items:

- [ ] CI is green on the latest commit of `release/YYYY.MM`.
- [ ] `atlas/version.py` and `pyproject.toml` agree on the new version.
- [ ] `CHANGELOG.md` has a populated `## [X.Y.Z] - YYYY-MM-DD` section.
- [ ] Manual smoke test against a packaged wheel (see
      [Smoke test](#smoke-test) below).
- [ ] No known P0/P1 bugs filed against the release branch.

#### 5. Tag and publish

When the checklist is green:

```bash
# From a local clone, checked out on release/YYYY.MM at the exact
# commit you want to ship.
git checkout release/YYYY.MM
git pull
git tag -a vX.Y.Z -m "Atlas UI 3 vX.Y.Z"
git push origin vX.Y.Z
```

Then create a GitHub Release from that tag using the web UI or:

```bash
gh release create vX.Y.Z \
  --title "vX.Y.Z" \
  --notes-file <(awk '/^## \[X\.Y\.Z\]/,/^## \[/' CHANGELOG.md | sed '$d') \
  --target release/YYYY.MM
```

Publishing a GitHub Release triggers `pypi-publish.yml`. Pushing the
`vX.Y.Z` tag also triggers `quay-publish.yml`. Watch both in the
Actions tab and confirm:

- `atlas-chat X.Y.Z` is visible on <https://pypi.org/project/atlas-chat/>.
- `quay.io/<namespace>/atlas-ui-3:X.Y.Z` and `:X.Y` tags exist.

#### 6. Carry the bump back to main

Open a PR merging `release/YYYY.MM` into `main`. This carries the
version bump commit and any hotfix cherry-picks that did not originate
on `main` back into the trunk. Fast-forward when possible; use a
no-ff merge commit if the branches have diverged.

After this merge, `main` is now at version `X.Y.Z` and ready to
accumulate PRs under a fresh `## [Unreleased]` section.

---

## Hotfix releases

For an urgent fix to an already-released minor version:

1. Branch from the release branch, not main:
   ```bash
   git checkout release/2026.05
   git checkout -b hotfix/2026.05-file-upload-fix
   ```
2. Commit the fix and a CHANGELOG entry under a new patch section:
   ```markdown
   ## [0.2.1] - 2026-05-12
   - **Fix**: ...
   ```
3. Bump `atlas/version.py` and `pyproject.toml` to the patch version
   (`0.2.0 → 0.2.1`).
4. PR into `release/2026.05`, get review, merge.
5. Tag `v0.2.1` on the release branch tip and push. Publishing is
   automatic from there.
6. Merge the hotfix commit (or cherry-pick) to `main` and open a PR
   so the fix is not lost on the next monthly cut.

Never hotfix from `main` — by the time a patch release is needed,
`main` has usually moved on and is not the state you shipped.

---

## Smoke test

Before tagging, install the candidate wheel into a clean venv and
verify the basics end-to-end:

```bash
# On the release branch, build a local wheel
uv build --wheel

# Install it into a throwaway venv
uv venv /tmp/atlas-smoke --python 3.11
/tmp/atlas-smoke/bin/python -m pip install dist/atlas_chat-X.Y.Z-py3-none-any.whl

# From a scratch directory
cd /tmp && rm -rf atlas-smoke-work && mkdir atlas-smoke-work && cd atlas-smoke-work
/tmp/atlas-smoke/bin/atlas-init --minimal
# Edit .env to add one real API key

# Version check (must match the release)
/tmp/atlas-smoke/bin/atlas-chat --version

# Headless LLM call
/tmp/atlas-smoke/bin/atlas-chat "Say hello" --model <your-configured-model>

# Tool call path
/tmp/atlas-smoke/bin/atlas-chat "What is 2+2?" \
  --tools calculator_evaluate --tool-choice-required

# Web server smoke
/tmp/atlas-smoke/bin/atlas-server --port 18000 &
curl -fsS http://127.0.0.1:18000/api/health | jq .version
kill %1
```

If any step fails, the release is **not** shippable. File a bug, fix
on `main`, cherry-pick to the release branch, and re-run the smoke
test before tagging.

For container images, pull the candidate and run `/api/health`:

```bash
podman run --rm -p 18000:8000 --env-file .env \
  quay.io/<namespace>/atlas-ui-3:X.Y.Z &
curl -fsS http://127.0.0.1:18000/api/health
```

Note: the Quay image is built on tag push. You will not have an image
to smoke-test until after you push the tag. If you want a pre-tag
image, build locally from the release branch using `podman build`.

---

## Rolling back a bad release

PyPI releases cannot be overwritten. If a release is broken:

1. **Yank** the bad version from PyPI with `twine upload --skip-existing`
   cannot help here; use the PyPI web UI → project → Manage → Yank.
   Yanking keeps existing installs working but hides the version from
   `pip install atlas-chat` resolution.
2. Cut a patch release (`X.Y.(Z+1)`) using the hotfix flow above. This
   is almost always the right fix — rolling forward beats rolling back.
3. For container images, retag `:latest` to the previous known-good:
   ```bash
   podman pull quay.io/<ns>/atlas-ui-3:X.Y.(Z-1)
   podman tag  quay.io/<ns>/atlas-ui-3:X.Y.(Z-1) quay.io/<ns>/atlas-ui-3:latest
   podman push quay.io/<ns>/atlas-ui-3:latest
   ```
4. Post-mortem: open an issue with `type:incident` and link every PR,
   the bad tag, and the fix. Add a CHANGELOG entry under the patch
   version noting the incident and the mitigation.

---

## Guardrails

The `release-cut.yml` workflow will **never**:

- Push a tag.
- Create a non-draft PR.
- Publish to PyPI, Quay, or any other registry.
- Touch any branch other than `release/YYYY.MM` and its internal bump
  commits.
- Run on a repo fork — scheduled workflows and PR creation are gated on
  `github.repository == 'sandialabs/atlas-ui-3'`.

What automation *does* do: cuts the branch, bumps version files,
reshapes `CHANGELOG.md`, and opens a draft PR with the checklist. Every
step from "run smoke tests" forward is manual and requires a human.

If you need to override automation (off-cycle release, custom version,
etc.), use the workflow's `workflow_dispatch` inputs. Use `dry_run:
true` first to preview.

---

## Open policy decisions

These are left for a maintainer decision; flag a PR against this doc
when one is made.

1. **SemVer vs CalVer.** Today we ship SemVer (`0.1.5`). A monthly
   cadence often pairs naturally with CalVer (`2026.5.0`), and the
   release branch is already named `release/YYYY.MM`. If we switch,
   the PyPI version jumps and downstreams pinning `atlas-chat<1.0`
   will break. Recommendation: keep SemVer, revisit at 1.0.
2. **Release captain rotation.** Today the cut PR is opened as a
   draft with no assignee. We could either auto-assign the prior
   captain, round-robin a list in `.github/CODEOWNERS`, or keep it
   manual. Manual is fine until we miss a release.
3. **Branch protection on `release/*`.** We should require CI-green
   and at least one review on merges into any `release/*` branch.
   That is a GitHub repo setting, not code; capture the decision here
   when it is applied.
4. **Announcement channel.** Where do external users learn about a
   new release? Options: GitHub Release notes only (current), plus a
   README badge, plus a mailing list. Pick one and document it here.
5. **Supported versions policy.** How many prior minor versions
   receive security patches? Current implicit answer: only the
   latest. Document an explicit N-1 or N-2 policy if users ask.

---

## Related files

- [.github/workflows/release-cut.yml](../../.github/workflows/release-cut.yml) — the cron automation
- [.github/workflows/pypi-publish.yml](../../.github/workflows/pypi-publish.yml) — triggers on GitHub Release
- [.github/workflows/quay-publish.yml](../../.github/workflows/quay-publish.yml) — triggers on `v*.*.*` tag push
- [.github/release-checklist.md](../../.github/release-checklist.md) — PR body used by automation
- [CHANGELOG.md](../../CHANGELOG.md) — format contract for release notes
- [AGENTS.md](../../AGENTS.md) — version-bump and changelog conventions for contributors
