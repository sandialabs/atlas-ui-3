# Documentation Bundling

Last updated: 2026-01-19

## Overview

The Atlas UI 3 project includes an automated documentation bundling system that creates a standalone archive of recent documentation. This bundle is designed to help AI agents and other tools understand how to interact with Atlas UI 3.

## What Gets Bundled

The documentation bundle (`atlas-ui-3-docs.zip`) includes:

- `/docs/admin/` - Administrative and operational documentation
- `/docs/developer/` - Developer guides and architecture documentation
- `/docs/example/` - Example configurations and use cases
- `/docs/getting-started/` - Installation and quick start guides
- `/docs/planning/` - Planning and roadmap documents
- `/docs/readme_img/` - Images and screenshots
- `/docs/README.md` - Documentation overview

**Note**: The `/docs/archive/` folder is intentionally excluded as it contains outdated or experimental documentation.

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

1. Place current/active documentation in the appropriate `/docs/` subdirectory
2. Move outdated documentation to `/docs/archive/` to exclude it from the bundle
3. Update `/docs/README.md` if adding a new documentation category
4. The bundle will automatically include your changes on the next CI/CD run
