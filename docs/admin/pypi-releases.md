# PyPI Release Guide

Last updated: 2026-09-24

This guide covers how to publish new versions of the `atlas-chat` package to PyPI.

## Prerequisites

- Write access to the repository
- Trusted publishers configured on PyPI and TestPyPI for this repository's `pypi-publish.yml` workflow and the `pypi` / `testpypi` GitHub environments
- `gh` CLI installed (for CLI-based releases)

## Publishing a New Release

### Option A: GitHub CLI (Recommended)

```bash
# 1. Update version in BOTH sources the publish workflow cross-checks
#    pyproject.toml:  version = "0.2.0"
#    atlas/version.py: VERSION = "0.2.0"
#    (a mismatch fails the publish build)

# 2. Refresh the lockfile the publish workflow validates
uv lock
#    (`uv lock --check` runs in pypi-publish.yml; a stale lock fails it)

# 3. Commit the version bump
git add pyproject.toml atlas/version.py uv.lock
git commit -m "Bump version to 0.2.0"
git push

# 4. Create tag and release (triggers PyPI publish)
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 --title "v0.2.0" --notes "Release notes here"
```

For the full release runbook (changelog reshape, release branch, smoke
test), see [docs/developer/release-process.md](../developer/release-process.md).

### Option B: GitHub Web UI

1. Update `version` in `pyproject.toml`, `VERSION` in `atlas/version.py`
   (they must agree), and run `uv lock` to refresh `uv.lock`; commit and push
2. Go to **Releases** → **Draft a new release**
3. Click **Choose a tag** → type `v0.2.0` → **Create new tag**
4. Title: `v0.2.0`
5. Description: Add release notes
6. Click **Publish release**

### Option C: Manual Workflow Dispatch

For publishing without creating a release:

1. Go to **Actions** → **Publish Python Package to PyPI**
2. Click **Run workflow**
3. Select branch (usually `main`)
4. Target: `pypi` (or `testpypi` for testing)
5. Click **Run workflow**

## Version Numbering

Follow semantic versioning (`MAJOR.MINOR.PATCH`):

- **MAJOR**: Breaking changes
- **MINOR**: New features, backward compatible
- **PATCH**: Bug fixes, backward compatible

## Testing with TestPyPI

Before publishing to production PyPI, you can test with TestPyPI:

1. Run workflow with target: `testpypi`
2. Test installation:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ atlas-chat
   ```

## Monitoring Releases

- **Workflow status**: Actions → Publish Python Package to PyPI
- **Published package**: https://pypi.org/project/atlas-chat/
- **Release artifacts**: Attached to GitHub Release

## Troubleshooting

### "invalid-publisher"

- Verify the trusted publisher exists on PyPI/TestPyPI for repository `sandialabs/atlas-ui-3`
- Ensure the workflow file name is still `pypi-publish.yml`
- Ensure the GitHub environment name still matches the publisher (`pypi` or `testpypi`)

### "Version already exists"

- PyPI doesn't allow overwriting versions
- Bump the version number in `pyproject.toml`

### Workflow not triggering

- Pushing a tag alone doesn't trigger the workflow
- You must create a **GitHub Release** from the tag
- Or use manual workflow dispatch

## Checklist for Releases

- [ ] Update version in `pyproject.toml`
- [ ] Update CHANGELOG.md
- [ ] Commit and push changes
- [ ] Create git tag matching version (`v0.2.0`)
- [ ] Create GitHub Release from tag
- [ ] Verify workflow completes successfully
- [ ] Test installation: `pip install atlas-chat==0.2.0`
