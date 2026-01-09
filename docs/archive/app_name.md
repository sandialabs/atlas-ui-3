# App Name Configuration

This document explains how to configure the application name that appears in the browser title and throughout the UI.

## Overview

The app name can be configured in two places:
- **Frontend (Browser Title)**: Set at build time using `VITE_APP_NAME`
- **Backend (API Response)**: Set at runtime using `APP_NAME`
 - **Optional Powered-By Badge**: Controlled via `VITE_FEATURE_POWERED_BY_ATLAS`

## Frontend Configuration (Browser Title)

The browser title is set at build time and cannot be changed without rebuilding the frontend.

### Local Development

1. Set the environment variable in your `.env` file:
```bash
VITE_APP_NAME=My Custom Chat App
```

2. Rebuild the frontend:
```bash
cd frontend
npm run build
```

You can also control whether the "Powered By Sandia ATLAS" badge appears on the welcome screen:

```bash
VITE_FEATURE_POWERED_BY_ATLAS=true  # show powered-by badge
```

Other deployments can set this flag to `false` (or omit it) to hide the badge while still customizing the primary logo and app name.

### Docker Builds

Use the `VITE_APP_NAME` build argument:

```bash
# Default build (uses "Chat UI")
docker build -t atlas-ui-3 .

# Custom app name
docker build --build-arg VITE_APP_NAME="Production Chat" -t atlas-ui-3 .

# Environment-specific builds
docker build --build-arg VITE_APP_NAME="Staging Environment" -t atlas-ui-3:staging .
docker build --build-arg VITE_APP_NAME="Development Build" -t atlas-ui-3:dev .
```

## Backend Configuration (API Response)

The backend serves the app name via the `/api/config` endpoint and can be changed at runtime.

### Environment Variable

Set `APP_NAME` in your `.env` file:
```bash
APP_NAME=My Runtime App Name
```

### Runtime Behavior

- The frontend JavaScript will update the displayed app name after calling `/api/config`
- This affects UI elements but not the browser title
- Changes take effect immediately without rebuilding

## Best Practices

1. **Consistency**: Use the same name for both `VITE_APP_NAME` and `APP_NAME`
2. **Environment Naming**: Include environment indicators in non-production builds:
   - `VITE_APP_NAME=Chat UI (Staging)`
   - `VITE_APP_NAME=Chat UI (Development)`
3. **Build Automation**: Set `VITE_APP_NAME` in your CI/CD pipeline for consistent branding

## Example Configurations

### Development
```bash
# .env
VITE_APP_NAME=Chat UI (Dev)
APP_NAME=Chat UI (Dev)
```

### Staging
```bash
# .env
VITE_APP_NAME=Chat UI (Staging)
APP_NAME=Chat UI (Staging)
```

### Production
```bash
# .env
VITE_APP_NAME=Corporate Chat Platform
APP_NAME=Corporate Chat Platform
```

## Troubleshooting

**Q: I changed `APP_NAME` but the browser title didn't update**
A: The browser title is set at build time using `VITE_APP_NAME`. You need to rebuild the frontend.

**Q: I rebuilt but still see the old name**
A: Clear your browser cache or use a hard refresh (Ctrl+F5).

**Q: The title shows `%VITE_APP_NAME%` literally**
A: The environment variable wasn't set during build. Ensure `VITE_APP_NAME` is available when running `npm run build`.