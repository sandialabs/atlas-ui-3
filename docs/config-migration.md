# Configuration & Runtime Directory Migration

This project migrated to a cleaner separation of code, configuration, and runtime artifacts.

## New Layout

```
config/
  defaults/    # Template / version-controlled baseline configs
  overrides/   # Editable overrides (mounted volume / env APP_CONFIG_OVERRIDES)
runtime/
  logs/        # Application logs (JSONL)
  feedback/    # User feedback JSON files
  uploads/     # Future file uploads
```

Legacy directories (still supported for backward compatibility):

```
backend/configfiles          -> config/defaults
backend/configfilesadmin     -> config/overrides
backend/logs                 -> runtime/logs
feedback (root)              -> runtime/feedback
```

## Environment Variables

You can customize locations:

- `APP_CONFIG_OVERRIDES` (default: `config/overrides`)
- `APP_CONFIG_DEFAULTS`  (default: `config/defaults`)
- `RUNTIME_FEEDBACK_DIR` (default: `runtime/feedback`)

## Code Changes

- `ConfigManager._search_paths` now searches new directories first, then legacy paths.
- Admin routes seed `config/overrides` from `config/defaults` or legacy dirs if empty.
- Feedback routes use `runtime/feedback` (override with `RUNTIME_FEEDBACK_DIR`).
- MCP tool manager chooses `config/overrides/mcp.json` with legacy fallback.

## Migration Steps (Already Applied)

1. Created `config/defaults` and copied existing `backend/configfiles` contents.
2. Created `config/overrides` and copied existing `backend/configfilesadmin` contents.
3. Added new runtime directories: `runtime/logs`, `runtime/feedback`.
4. Updated `.gitignore` to exclude runtime artifacts.
5. Added backward-compatible search paths so no immediate breakage.

## Next Clean-Up (Optional)

- Remove legacy `backend/configfiles*` once confident no tooling relies on them.
- Update any deployment manifests / Docker volumes to mount `config/overrides` & `runtime`.
- Document environment variables in main README.

## Rollback

If needed, you can restore previous behavior by setting:

```
APP_CONFIG_OVERRIDES=backend/configfilesadmin
APP_CONFIG_DEFAULTS=backend/configfiles
RUNTIME_FEEDBACK_DIR=feedback
```

---
Generated on migration date.
