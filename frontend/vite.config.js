import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import crypto from 'crypto'

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

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        configure: (proxy, options) => {
          proxy.on('error', (err, req, res) => {
            console.log('proxy error', err);
          });
          proxy.on('proxyReq', (proxyReq, req, res) => {
            console.log('Sending Request to the Target:', req.method, req.url);
          });
          proxy.on('proxyRes', (proxyRes, req, res) => {
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
