import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// Link previews (X, LinkedIn, Slack) need an absolute og:image. On Vercel the build knows the
// production domain; on another host set SITE_URL. Without either the path stays relative.
const vercel = process.env.VERCEL_PROJECT_PRODUCTION_URL
const site = (process.env.SITE_URL ?? (vercel === undefined ? '' : `https://${vercel}`)).replace(/\/$/, '')

const absoluteOgImage: Plugin = {
  name: 'absolute-og-image',
  transformIndexHtml: (html) => html.replace('content="/og.png"', `content="${site}/og.png"`),
}

export default defineConfig({
  plugins: [react(), absoluteOgImage],
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
