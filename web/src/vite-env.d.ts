/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** '1' in the showcase build: recorded runs as static files, no server (src/showcase/config.ts) */
  readonly VITE_SHOWCASE?: string
}
