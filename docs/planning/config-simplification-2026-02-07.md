# Config Directory Simplification

Last updated: 2026-02-07

## Problem

The config system had too many layers and was confusing:
- `config/defaults/` - tracked default configs
- `config/overrides/` - local overrides (partially gitignored)
- `atlas/config/defaults/` - package-embedded copy (synced via script)
- `ConfigManager._search_paths()` checked 10+ locations with complex priority
- `APP_CONFIG_OVERRIDES` and `APP_CONFIG_DEFAULTS` env vars added more complexity
- `mcp-example-configs/` only existed under `config/`, not shipped with the package

## Solution: Two-Layer Config

**`atlas/config/`** = Package defaults (tracked, shipped with pip install)
**`config/`** = User's local config (gitignored, created by `atlas-init`)

### Lookup Order

1. `APP_CONFIG_DIR` env var (defaults to `config/`) - user customizations
2. `atlas/config/` - package defaults (always available)

### Directory Structure

```
atlas/config/                    # Tracked in git, shipped with package
    llmconfig.yml
    mcp.json
    rag-sources.json
    compliance-levels.json
    domain-whitelist.json
    file-extractors.json
    help-config.json
    splash-config.json
    mcp-example-configs/         # Moved from config/mcp-example-configs/
        mcp-calculator.json
        mcp-filesystem.json
        ...

config/                          # Gitignored, user's local copy
    llmconfig.yml
    mcp.json
    ...
```

## Changes Made

### 1. `atlas/config/` directory restructure
- Flattened `atlas/config/defaults/` to `atlas/config/`
- Copied `config/mcp-example-configs/` into `atlas/config/mcp-example-configs/`
- Removed `llmconfig-buggy.yml` (test artifact, not needed in package)

### 2. `ConfigManager` (`atlas/modules/config/config_manager.py`)
- Replaced `app_config_overrides` + `app_config_defaults` with single `app_config_dir`
- Replaced `APP_CONFIG_OVERRIDES` + `APP_CONFIG_DEFAULTS` env vars with `APP_CONFIG_DIR`
- Simplified `_search_paths()` to check: `config_dir / file` then `atlas/config/ / file`
- Removed legacy fallback paths (`configfilesadmin`, `configfiles`)

### 3. `atlas-init` (`atlas/init_cli.py`)
- Updated to copy from `atlas/config/` (not `atlas/config/defaults/`)
- Also copies `mcp-example-configs/` directory
- Updated `--minimal` .env template: `APP_CONFIG_DIR` instead of `APP_CONFIG_OVERRIDES`

### 4. CLI tools
- `server_cli.py`: `--config-folder` sets `APP_CONFIG_DIR` (was `APP_CONFIG_OVERRIDES`)
- `atlas_chat_cli.py`: `--config-overrides` renamed to `--config-dir`, sets `APP_CONFIG_DIR`
- Removed `--config-defaults` flag from both (no longer needed)

### 5. Admin routes (`atlas/routes/admin_routes.py`)
- `setup_config_overrides()` renamed to `setup_config_dir()`
- Uses `app_config_dir` instead of `app_config_overrides`
- MCP server marketplace reads from `atlas/config/mcp-example-configs/`

### 6. Config routes (`atlas/routes/config_routes.py`)
- Banner path uses `app_config_dir`

### 7. MCP client (`atlas/modules/mcp_tools/client.py`)
- Uses `app_config_dir` for config path resolution

### 8. Package config
- `pyproject.toml`: updated `package-data` paths
- `.gitignore`: added `config/` (entire directory, user-local)
- Removed `scripts/sync_package_assets.sh` (no longer needed)

### 9. Tests
- Updated `test_config_manager_paths.py` assertions
- Updated `test_config_manager.py` attribute checks

## Migration

For existing users with `config/overrides/` or `config/defaults/`:
- Run `atlas-init` to get a fresh `config/` from package defaults
- Or manually: move files from `config/overrides/` or `config/defaults/` to `config/`
- Replace `APP_CONFIG_OVERRIDES` with `APP_CONFIG_DIR` in `.env`
