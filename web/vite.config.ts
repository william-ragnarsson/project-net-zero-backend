import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        // The SSE stream stays open for the whole run: 0 disables both proxy timeouts,
        // and an identity encoding keeps the backend from compressing (and so buffering) it.
        timeout: 0,
        proxyTimeout: 0,
        headers: { 'accept-encoding': 'identity' },
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  test: {
    include: ['src/**/*.test.{ts,tsx}'],
    environment: 'node',
  },
})
