import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import crypto from 'crypto'
import { readFileSync } from 'fs'
import { execSync } from 'child_process'

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
  }

  console.log('\n[atlas-ui-3] Vite build-time environment:')
  for (const [key, value] of Object.entries(varsToLog)) {
    console.log(`  ${key}=${value ?? '<undefined>'}`)
  }
  console.log('')
}

logViteEnv()

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
export default defineConfig({
  plugins: [react()],
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
})
