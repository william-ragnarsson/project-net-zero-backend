import type { Config } from 'tailwindcss'

// Tokens copied from the website (project-net-zero-website/tailwind.config.ts); keep them in sync.
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        dark: '#0a0a0a',
        'dark-card': '#111111',
        'dark-border': '#1a1a1a',
        neon: '#00ff88',
        'neon-dim': '#00cc6a',
        muted: '#848b98',
        caution: '#ffbd2e',
        fail: '#ff5f57',
      },
      fontFamily: {
        sans: ['var(--font-geologica)', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['var(--font-jetbrains)', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
