import type { Config } from 'tailwindcss'

// The app's light theme. It departs from the dark marketing site on purpose; tokens live as CSS
// variables in index.css so the tree's SVG can use the same values.
const v = (name: string) => `var(--nz-${name})`

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    colors: {
      transparent: 'transparent',
      current: 'currentColor',
      white: '#ffffff',
      page: v('page'),
      sidebar: v('sidebar'),
      surface: v('surface'),
      hover: v('hover'),
      line: v('line'),
      control: v('control'),
      'control-strong': v('control-strong'),
      edge: v('edge'),
      'edge-strong': v('edge-strong'),
      ink: v('ink'),
      'ink-2': v('ink-2'),
      'ink-3': v('ink-3'),
      accent: v('accent'),
      'accent-hover': v('accent-hover'),
      'accent-tint': v('accent-tint'),
      fail: v('fail'),
      'fail-tint': v('fail-tint'),
      caution: v('caution'),
      'code-keyword': v('code-keyword'),
      'code-string': v('code-string'),
      scrim: v('scrim'),
    },
    fontFamily: {
      sans: ['Geologica', 'ui-sans-serif', 'system-ui', 'sans-serif'],
      mono: ['"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
    },
    fontSize: {
      xs: ['12px', '16px'],
      sm: ['13px', '20px'],
      base: ['14px', '21px'],
      md: ['15px', '22px'],
      lg: ['20px', '28px'],
      xl: ['28px', '34px'],
      code: ['12px', '18px'],
    },
    fontWeight: { normal: '400', medium: '500' },
    borderRadius: { none: '0', sm: '4px', DEFAULT: '6px', md: '6px', lg: '8px', full: '9999px' },
    boxShadow: { none: 'none', float: '0 8px 24px rgba(0, 0, 0, 0.08)' },
    extend: {
      // `colors` has no gray, so without these a bare `border`/`divide-*` falls back to currentColor
      // (ink) and a bare `ring` to Tailwind's blue.
      borderColor: { DEFAULT: v('line') },
      ringColor: { DEFAULT: v('ink-2') },
      keyframes: {
        breathe: { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.35' } },
        'sheet-in': { from: { opacity: '0', transform: 'translateX(-8px)' }, to: { opacity: '1', transform: 'none' } },
        'fade-in': { from: { opacity: '0' }, to: { opacity: '1' } },
      },
      animation: {
        breathe: 'breathe 2s ease-in-out infinite',
        'sheet-in': 'sheet-in 200ms cubic-bezier(0.22, 1, 0.36, 1)',
        'fade-in': 'fade-in 150ms ease-out',
      },
      transitionTimingFunction: { out: 'cubic-bezier(0.22, 1, 0.36, 1)' },
    },
  },
  plugins: [],
} satisfies Config
