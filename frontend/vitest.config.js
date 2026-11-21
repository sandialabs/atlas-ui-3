import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { retroThemesModulePath } from './src/themes/retro/retroThemesBuildFlag.js'

export default defineConfig({
  plugins: [react()],
  // Tests run against the full catalog; retro-themes-feature-flag.test.jsx
  // covers the compiled-out module directly.
  resolve: {
    alias: {
      '@atlas/retro-themes': retroThemesModulePath(true),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.js'],
    globals: true,
    exclude: [
      '**/node_modules/**',
      '**/dist/**',
      '**/cypress/**',
      '**/.{idea,git,cache,output,temp}/**',
      '**/e2e/**', // Exclude E2E tests from vitest
    ],
  },
})