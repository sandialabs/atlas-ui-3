import { defineConfig, loadEnv } from 'vite'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import crypto from 'crypto'
import { readFileSync } from 'fs'
import { execSync } from 'child_process'
import { retroThemesBuildEnabled, retroThemesModulePath } from './src/themes/retro/retroThemesBuildFlag.js'
import { readRetroCssSources, retroCssPlugin, retroCssVersion } from './src/themes/retro/retroCssPlugin.js'

// Configure crypto to use FIPS-compliant algorithms if FIPS mode is enabled
// This wrapper ensures we use SHA-256 instead of MD4/MD5 which are disabled in FIPS
const originalCreateHash = crypto.createHash
crypto.createHash = function (algorithm, options) {
  // Replace non-FIPS algorithms with FIPS-compliant SHA-256
  if (algorithm === 'md4' || algorithm === 'md5') {
    algorithm = 'sha256'
  }
  return originalCreateHash.call(this, algorithm, options)
}

// Log key VITE environment variables when the config is evaluated
// so that `npm run build` and other commands clearly show which
// branding-related settings are in effect.
const logViteEnv = () => {
  const varsToLog = {
    VITE_APP_NAME: process.env.VITE_APP_NAME,
    VITE_FEATURE_POWERED_BY_ATLAS: process.env.VITE_FEATURE_POWERED_BY_ATLAS,
    VITE_FEATURE_ANIMATED_LOGO: process.env.VITE_FEATURE_ANIMATED_LOGO,
    VITE_FEATURE_RAG_CITATIONS: process.env.VITE_FEATURE_RAG_CITATIONS,
    VITE_FEATURE_RETRO_THEMES: process.env.VITE_FEATURE_RETRO_THEMES,
    VITE_WELCOME_MESSAGE: process.env.VITE_WELCOME_MESSAGE,
  }

  console.log('\n[atlas-ui-3] Vite build-time environment:')
  for (const [key, value] of Object.entries(varsToLog)) {
    console.log(`  ${key}=${value ?? '<undefined>'}`)
  }
  console.log('')
}

// Read app version: env var (Docker) -> atlas/version.py (local dev) -> 'unknown'
function getAppVersion() {
  if (process.env.APP_VERSION) return process.env.APP_VERSION
  try {
    const versionFile = readFileSync(new URL('../atlas/version.py', import.meta.url), 'utf-8')
    const match = versionFile.match(/VERSION\s*=\s*"([^"]+)"/)
    return match ? match[1] : 'unknown'
  } catch {
    return 'unknown'
  }
}

// Read short git commit hash: env var (Docker) -> git command (local dev) -> 'unknown'
function getGitHash() {
  if (process.env.GIT_HASH) return process.env.GIT_HASH
  try {
    return execSync('git rev-parse --short HEAD').toString().trim()
  } catch {
    return 'unknown'
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  // Resolve the documented build flag from Vite's .env/.env.local files;
  // explicit process/Docker build arguments take precedence.
  const env = loadEnv(mode, fileURLToPath(new URL('.', import.meta.url)), 'VITE_')
  const retroThemesEnabled = retroThemesBuildEnabled({ ...env, ...process.env })
  process.env.VITE_FEATURE_RETRO_THEMES = String(retroThemesEnabled)
  process.env.VITE_RETRO_CSS_VERSION = retroThemesEnabled
    ? retroCssVersion(readRetroCssSources()) : ''
  logViteEnv()
  console.log('[atlas-ui-3] Retro themes ' +
    (retroThemesEnabled ? 'included in' : 'compiled out of') + ' this build\n')
  return {
  plugins: [react(), retroCssPlugin(retroThemesEnabled)],
  resolve: {
    alias: {
      '@atlas/retro-themes': retroThemesModulePath(retroThemesEnabled),
    },
  },
  define: {
    __APP_VERSION__: JSON.stringify(getAppVersion()),
    __GIT_HASH__: JSON.stringify(getGitHash()),
    __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (proxyReq, req) => {
            console.log('Sending Request to the Target:', req.method, req.url);
          });
          proxy.on('proxyRes', (proxyRes, req) => {
            console.log('Received Response from the Target:', proxyRes.statusCode, req.url);
          });
        }
      },
      '/admin': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on('error', (err) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (proxyReq, req) => {
            console.log('Sending Request to the Target:', req.method, req.url);
          });
          proxy.on('proxyRes', (proxyRes, req) => {
            console.log('Received Response from the Target:', proxyRes.statusCode, req.url);
          });
        }
      },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true
      }
    }
  }
}})
