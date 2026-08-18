# Release Process

Last updated: 2026-08-16

This document is the canonical runbook for cutting a release of Atlas
UI 3. If you are about to publish a version, follow
[Cutting a release](#cutting-a-release) top-to-bottom.

---

## Philosophy

Atlas UI 3 **ships from `main`**. A release is a version-bump PR, a tag
on the resulting commit, and a GitHub Release — the rest is automation.
Roughly monthly is the habit, not a rule; ship when there is something
worth shipping.

The model is small and deliberately boring:

- **`main`** is trunk. It is always deployable. Every PR on `main` must
  pass CI and add a `CHANGELOG.md` entry under `## [Unreleased]`.
- **The version bump lands on `main`** like any other PR, so `main`
  always equals the last shipped version. There is nothing to back-merge
  and no release branch to keep alive.
- **`vX.Y.Z`** is the tag that ships. Publishing a GitHub Release (created
  from a `v*.*.*` tag) triggers `pypi-publish.yml`; pushing a `v*.*.*` tag
  triggers `quay-publish.yml`. This is the intended release path.

A stabilization branch (`release/YYYY.MM`, cut by `release-cut.yml`)
remains available for the case it actually solves — freezing a release
while unrelated work keeps landing on `main`. It is the exception, not
the default; see
[When to use a stabilization branch instead](#when-to-use-a-stabilization-branch-instead).

Humans make the go/no-go call and push the tag. Publishing to PyPI is
irreversible — a version can be yanked but never replaced — so treat
`gh release create` as the point of no return.

### Other publish paths (non-release)

Two publish paths exist outside the tag-driven release flow. Both are
preserved deliberately, but they should not be used to ship a version
to end users:

- **`quay-publish.yml` also fires on `push` to `main`, `develop`, and
  `quay`.** Every merge to `main` builds and pushes a container image
  tagged with the branch name and commit SHA. Those images are for
  continuous smoke-testing, not for external consumption. The
  `X.Y.Z` and `X.Y` semver tags only appear on a `v*.*.*` tag push.
- **`pypi-publish.yml` exposes a `workflow_dispatch` with a `target`
  input** (`testpypi` or `pypi`). This is an emergency escape hatch to
  re-publish the current `main` build if the tag-driven run failed and
  the tag cannot be recreated. Using `target: pypi` publishes to
  production PyPI immediately — only maintainers should run it, and only
  as a last resort. Prefer cutting a patch release via the hotfix flow
  over reaching for this lever.

  `target: testpypi` authenticates through **OIDC trusted publishing**,
  not a token secret. The matching publisher on test.pypi.org is scoped
  to project `atlas-chat`, owner `sandialabs`, repo `atlas-ui-3`,
  workflow `pypi-publish.yml`, environment `testpypi` — renaming the
  workflow file or the `testpypi` environment breaks the exchange with
  `invalid-publisher`, which is a publisher-config error on PyPI's side
  and not something a repo secret can fix. `target: pypi` still uses the
  `PYPI_API_TOKEN` secret.

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

## Cutting a release

A release is three things: a version-bump commit on `main`, a tag on
that commit, and a GitHub Release. Everything after the tag is
automation. There is no stabilization branch and no back-merge — the
bump lands on `main` directly, so `main` is never behind what shipped.

Budget about ten minutes of hands-on work, plus the publish runs.

### 1. Pick the version

Read the next version off the **highest published release**, not off
`pyproject.toml`:

```bash
gh release list -L 3          # or: git tag --sort=-v:refname | head
```

Because the bump lands on `main`, `pyproject.toml` there equals the
*last shipped* version — it is the current version, never the next one.
Normal release: bump MINOR. Fix on top of a shipped version: bump PATCH.

### 2. Bump and open the PR

Do the work on a short-lived branch cut from `main` — ideally in a
worktree, so you do not disturb whatever you have checked out:

```bash
V=0.6.0
git fetch origin main
git worktree add -b release/$V ../atlas-release-$V origin/main
cd ../atlas-release-$V
```

Three files change, in one commit:

- `atlas/version.py` — `VERSION = "X.Y.Z"`
- `pyproject.toml` — the top-level `version = "X.Y.Z"` (not a
  dependency pin that happens to match)
- `CHANGELOG.md` — the `## [Unreleased]` heading becomes
  `## [X.Y.Z] - YYYY-MM-DD`, with a fresh empty `## [Unreleased]`
  inserted above it. The entries themselves are not touched; they were
  written by the PRs that landed them.

```bash
python3 - "$V" <<'PY'
import pathlib, re, sys, datetime
version = sys.argv[1]
today = datetime.date.today().isoformat()

for path, key in (("atlas/version.py", "VERSION"), ("pyproject.toml", "version")):
    p = pathlib.Path(path)
    new, n = re.subn(rf'^{key} = "[^"]+"', f'{key} = "{version}"',
                     p.read_text(), count=1, flags=re.M)
    if n != 1:
        raise SystemExit(f"{path}: expected 1 {key} line, found {n}")
    p.write_text(new)

p = pathlib.Path("CHANGELOG.md")
text = p.read_text()
m = re.search(r'^## \[Unreleased\][ \t]*$', text, re.M)
if not m:
    raise SystemExit("CHANGELOG.md has no '## [Unreleased]' section")
p.write_text(text[:m.start()] + "## [Unreleased]\n\n"
             + f"## [{version}] - {today}" + text[m.end():])
print(f"bumped to {version} ({today})")
PY

git commit -am "chore(release): v$V"
git push -u origin "release/$V"
gh pr create --base main --title "release: $V" --body "Version bump for the $V release."
```

The `pypi-publish.yml` build job re-checks that `atlas/version.py` and
`pyproject.toml` agree and fails the publish if they do not, so a
half-applied bump cannot ship.

### 3. Merge

CI on the bump PR is the gate. The diff is three lines of metadata on
top of a `main` that was already green, so this is a formality — but do
not skip it, because the changelog reshape can be malformed in ways
only a build catches.

```bash
gh pr merge <number> --squash --delete-branch
```

### 4. Tag and publish

Tag the squashed commit on `main` — not the branch you just deleted:

```bash
git fetch origin main
git tag -a "v$V" -m "Atlas UI 3 v$V" origin/main
git push origin "v$V"
```

Then extract this version's changelog section and publish the Release.
Do not try to inline the extraction with process substitution; it is
fragile and does not handle the first-ever release (which has no
following `## [` to delimit the section):

```bash
python3 - "$V" > /tmp/release-notes.md <<'PY'
import re, sys, pathlib
version = sys.argv[1]
text = pathlib.Path("CHANGELOG.md").read_text()
# Grab the block starting at `## [<version>]` up to the next `## [` or EOF.
m = re.search(rf'(^## \[{re.escape(version)}\][^\n]*\n.*?)(?=^## \[|\Z)',
              text, flags=re.MULTILINE | re.DOTALL)
if not m:
    sys.exit(f"No `## [{version}]` section in CHANGELOG.md")
sys.stdout.write(m.group(1).rstrip() + "\n")
PY

gh release create "v$V" --title "v$V" --verify-tag \
  --notes-file /tmp/release-notes.md
```

Two workflows fire:

- Pushing the tag triggers **`quay-publish.yml`** → `X.Y.Z`, `X.Y`, `X`,
  `latest`, and `vX.Y.Z-<sha>` image tags.
- Publishing the Release triggers **`pypi-publish.yml`** → builds the
  frontend, bundles `frontend/dist` into `atlas/static`, uploads the
  wheel and sdist to PyPI, then attaches both to the GitHub Release.

### 5. Verify

```bash
gh run list -L 5                                    # both publishes green
curl -s https://pypi.org/simple/atlas-chat/ | grep "$V"
curl -s "https://quay.io/api/v1/repository/<ns>/atlas-ui-3/tag/?onlyActiveTags=true" \
  | python3 -c "import json,sys; print([t['name'] for t in json.load(sys.stdin)['tags']][:10])"
```

The PyPI **JSON** API (`/pypi/atlas-chat/json`) is cached and can report
the previous version for several minutes after a successful upload —
check the simple index instead before concluding the publish failed.

Then remove the release worktree. Nothing else is owed: no back-merge,
no branch to keep alive.

> **Publishing is one-way.** A PyPI version cannot be replaced, only
> yanked and superseded. Everything before `gh release create` is
> reversible; nothing after it is. See
> [Rolling back a bad release](#rolling-back-a-bad-release).

---

## When to use a stabilization branch instead

`release-cut.yml` (cron, 14:00 UTC on the 22nd) opens a draft
`release/YYYY.MM` PR: it creates the branch from `main`, applies the
same three-file bump, reshapes the changelog, and fills the PR body
from [.github/release-checklist.md](../../.github/release-checklist.md).
Run it by hand with **Actions → Release: cut monthly branch → Run
workflow** (`version` overrides the computed bump; `dry_run: true`
prints the plan without pushing).

Reach for it only when a release genuinely needs a **freeze**: work you
do not want in the release keeps landing on `main` while the release
stabilizes, so fixes must be cherry-picked onto a branch that is held
back. That is the entire benefit, and it costs a branch, a checklist
PR, a cherry-pick discipline, and a back-merge afterwards.

When `main` is already shippable — the normal case — those steps buy
nothing, and the four steps above are the flow.

If you do take this path, the details that bite:

- **Reconcile the version state first.** The cron is idempotent only
  *within* a month; it does not reason about releases that already
  shipped. Read the next version from the highest published release,
  and close any superseded release PR (a recovery-path PR from a prior
  month can linger carrying a no-op bump like `0.2.0 → 0.2.0`) rather
  than adopting it as the release vehicle.
- **The cut PR doubles as the back-merge PR.** It already targets
  `main`; merge it after tagging. Do not open a second one.
- **CI may need a manual kick.** With `RELEASE_PAT` configured the PR
  is opened with the PAT and CI runs normally. Without it the workflow
  falls back to `GITHUB_TOKEN`, which deliberately does not trigger
  `pull_request` workflows — close and immediately reopen the PR, or
  push an empty commit from a personal account. The PR body carries a
  `CI kick required` banner when this fallback is in effect. (One-time
  fix: store a PAT with `repo` + `workflow`, or a fine-grained token
  with `Contents: write`, `Pull requests: write`, `Actions: write`, as
  the `RELEASE_PAT` secret.)
- **Only fixes land during the freeze.** Crashes, data loss, security,
  install/import failures, and regressions since the last release
  qualify. Features, refactors, and docs-only changes stay on `main`.
  Land the fix on `main` first, then `git cherry-pick -x <sha>` onto
  `release/YYYY.MM`, and add it under that release's CHANGELOG section
  on the branch.

The workflow will **never** push a tag, create a non-draft PR, publish
to any registry, or touch a branch other than `release/YYYY.MM`.
Scheduled runs are gated on `github.repository == 'sandialabs/atlas-ui-3'`
so forks do not cut releases. It is idempotent: branch plus open PR is
a no-op; branch without an open PR takes a recovery path that opens a
draft PR without rewriting the branch.

---

## Hotfix releases

Because releases ship from `main`, an urgent fix is an ordinary
release at a PATCH version: land the fix on `main` as a normal PR, then
run [Cutting a release](#cutting-a-release) bumping `0.2.0 → 0.2.1`.
Nothing special is required.

That works as long as everything else sitting on `main` is also
shippable — which is the usual case, since `main` is kept deployable.

**When it is not**, `main` has moved on and shipping it would carry
changes you are not ready to release. Only then, branch from the tag
you shipped and treat that branch as the release:

```bash
git checkout -b hotfix/0.2.1 v0.2.0
# commit the fix, bump both version files to 0.2.1, add a
# `## [0.2.1] - YYYY-MM-DD` CHANGELOG section
git push -u origin hotfix/0.2.1
git tag -a v0.2.1 -m "Atlas UI 3 v0.2.1" && git push origin v0.2.1
gh release create v0.2.1 --verify-tag --notes-file /tmp/release-notes.md
```

Then open a PR carrying the fix (and the bump) back to `main`, so the
next release does not regress it. Prefer cherry-picking the fix onto
`main` first and re-verifying there, rather than trusting that a fix
validated against the old tag still holds against current trunk.

---

## Smoke test

Optional, and CI is the real gate — reach for this when a release
touches packaging, the CLI entry points, or the bundled frontend, or
when you want a real-LLM check that no test suite covers.

The honest version of this test uses the artifact CI builds, not one
you build locally: after merging the bump PR but **before tagging**,
dispatch **Actions → Publish Python Package to PyPI → `target:
testpypi`**, then install from there.

```bash
uv venv /tmp/atlas-smoke --python 3.11
/tmp/atlas-smoke/bin/python -m pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  "atlas-chat==X.Y.Z"

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
  --tools calculator_evaluate

# Web server smoke
/tmp/atlas-smoke/bin/atlas-server --port 18000 &
curl -fsS http://127.0.0.1:18000/api/health | jq .version
kill %1
```

If any step fails, the release is **not** shippable — and since nothing
is tagged yet, there is nothing to undo. Fix it on `main`, then release
from there; the TestPyPI upload of the aborted version is harmless
(re-dispatching at the same version is a no-op, since the job passes
`skip-existing`).

For container images, pull the candidate and run `/api/health`:

```bash
podman run --rm -p 18000:8000 --env-file .env \
  quay.io/<namespace>/atlas-ui-3:X.Y.Z &
curl -fsS http://127.0.0.1:18000/api/health
```

Note: the semver-tagged Quay image is built on tag push, so there is no
`X.Y.Z` image to smoke-test until after you tag. There *is* a
`main-<sha>` image for every merge to `main`, including the bump
commit — pull that if you want a pre-tag image, or build locally with
`podman build`.

---

## Rolling back a bad release

PyPI releases cannot be overwritten. If a release is broken:

1. **Yank the bad version from PyPI.** There is no CLI yank — `twine`
   only uploads. Use the PyPI web UI → project → Manage → Yank.
   Yanking keeps existing installs working but hides the version from
   `pip install atlas-chat` resolution.
2. Ship a patch release (`X.Y.(Z+1)`) using the
   [hotfix flow](#hotfix-releases) above. This is almost always the
   right fix — rolling forward beats rolling back.
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

## Open policy decisions

These are left for a maintainer decision; flag a PR against this doc
when one is made.

1. **SemVer vs CalVer.** Today we ship SemVer (`0.5.0`). If we switch
   to CalVer (`2026.8.0`), the PyPI version jumps and downstreams
   pinning `atlas-chat<1.0` will break. Recommendation: keep SemVer,
   revisit at 1.0.
2. **Keep the monthly cron?** `release-cut.yml` still fires on the
   22nd and opens a draft `release/YYYY.MM` PR that nobody is
   obligated to use. It is harmless (it never publishes) but it does
   manufacture a branch and a PR every month. Options: leave it,
   restrict it to `workflow_dispatch`, or delete it and keep the
   stabilization path as a documented manual procedure.
3. **Should the bump PR require a review?** `main` currently requires
   a PR but zero approvals, which is what makes the four-step flow
   fast. A release is exactly when a second pair of eyes is cheapest
   and most valuable — decide whether that is worth the latency.
4. **Announcement channel.** Where do external users learn about a
   new release? Options: GitHub Release notes only (current), plus a
   README badge, plus a mailing list. Pick one and document it here.
5. **Supported versions policy.** How many prior minor versions
   receive security patches? Current implicit answer: only the
   latest. Document an explicit N-1 or N-2 policy if users ask.

---

## Related files

- [.github/workflows/release-cut.yml](../../.github/workflows/release-cut.yml) — the cron automation, used only by the stabilization-branch path
- [.github/workflows/pypi-publish.yml](../../.github/workflows/pypi-publish.yml) — publishes on GitHub Release; also has a `workflow_dispatch` escape hatch (`target: testpypi` for a pre-tag smoke artifact)
- [.github/workflows/quay-publish.yml](../../.github/workflows/quay-publish.yml) — publishes semver-tagged images on `v*.*.*` tag push, and branch-named images on push to `main`/`develop`/`quay`
- [.github/release-checklist.md](../../.github/release-checklist.md) — PR body used by automation
- [CHANGELOG.md](../../CHANGELOG.md) — format contract for release notes
- [AGENTS.md](../../AGENTS.md) — version-bump and changelog conventions for contributors
