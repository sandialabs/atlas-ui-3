# Documentation Bundling

Last updated: 2026-06-22

## Overview

The Atlas UI 3 project includes an automated documentation bundling system that creates a standalone archive of recent documentation. This bundle is designed to help AI agents and other tools understand how to interact with Atlas UI 3.

## What Gets Bundled

The documentation bundle (`atlas-ui-3-docs.zip`) contains the **entire `/docs/` tree** as it exists at build time, with two exceptions:

- **`/docs/archive/`** is excluded - it holds completed/superseded plans and investigations that no longer describe how the system currently works.
- Python caches (`__pycache__/`, `*.pyc`) are excluded as noise.

Everything else - `getting-started/`, `user-guide/`, `admin/`, `developer/` (including `developer/design-notes/`), `agentportal/`, `telemetry/`, `planning/`, `testing/`, `example/`, image folders, and the top-level `README.md` - is included automatically. There is no per-directory allowlist to maintain: drop a new doc into the right category and it ships on the next build.

## Generating the Bundle Locally

You can generate the documentation bundle manually using the bundling script:

```bash
# From the project root
bash scripts/bundle-docs.sh

# Or specify an output directory
bash scripts/bundle-docs.sh /path/to/output
```

This creates an `atlas-ui-3-docs.zip` file in the specified directory (or project root if not specified).

## CI/CD Integration

The documentation bundle is automatically generated and uploaded as an artifact in the CI/CD pipeline:

- **Workflow**: `.github/workflows/build-artifacts.yml`
- **Trigger**: Runs on pushes to `main` and `develop` branches, pull requests to `main`, or manual workflow dispatch
- **Artifact Name**: `atlas-ui-3-docs`
- **Retention**: 30 days

### Accessing the Documentation Bundle

1. Go to the [Actions tab](https://github.com/sandialabs/atlas-ui-3/actions) in the repository
2. Select a successful workflow run from the "Build Artifacts" workflow
3. Download the `atlas-ui-3-docs` artifact from the artifacts section

## Use Cases

The documentation bundle is particularly useful for:

- **AI Agents**: Providing context about the Atlas UI 3 architecture, configuration, and usage patterns
- **Offline Access**: Having a complete documentation package without network access
- **Distribution**: Sharing documentation with team members or stakeholders
- **Version Tracking**: Each build creates a snapshot of the documentation at that point in time

## Maintenance

When adding new documentation:

1. Place the doc in the appropriate `/docs/` subdirectory (see the "Where does a new doc go?" table in [`/docs/README.md`](../README.md)).
2. Link it from that directory's `README.md` index. `scripts/check-docs.sh` (run in the Build Artifacts workflow) fails the build if a doc is orphaned or a relative link is broken.
3. Move a doc to `/docs/archive/` once it stops describing how the system currently works - that excludes it from the bundle.
4. Update the area map in `/docs/README.md` only when you add a brand-new top-level category.
5. The bundle automatically includes your changes on the next CI/CD run.
