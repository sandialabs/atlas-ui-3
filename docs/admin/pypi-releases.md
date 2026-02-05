# PyPI Release Guide

Last updated: 2026-02-05

This guide covers how to publish new versions of the `atlas-chat` package to PyPI.

## Prerequisites

- Write access to the repository
- `PYPI_API_TOKEN` secret configured in GitHub repository settings
- `gh` CLI installed (for CLI-based releases)

## Publishing a New Release

### Option A: GitHub CLI (Recommended)

```bash
# 1. Update version in pyproject.toml
#    version = "0.2.0"

# 2. Commit the version bump
git add pyproject.toml
git commit -m "Bump version to 0.2.0"
git push

# 3. Create tag and release (triggers PyPI publish)
git tag v0.2.0
git push origin v0.2.0
gh release create v0.2.0 --title "v0.2.0" --notes "Release notes here"
```

### Option B: GitHub Web UI

1. Update `version` in `pyproject.toml` and push
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

1. Add `TEST_PYPI_API_TOKEN` secret to repository
2. Run workflow with target: `testpypi`
3. Test installation:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ atlas-chat
   ```

## Monitoring Releases

- **Workflow status**: Actions → Publish Python Package to PyPI
- **Published package**: https://pypi.org/project/atlas-chat/
- **Release artifacts**: Attached to GitHub Release

## Troubleshooting

### "Invalid API token"

- Verify `PYPI_API_TOKEN` is set in repository secrets
- Ensure token has upload permissions for `atlas-chat`
- Token should start with `pypi-`

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
