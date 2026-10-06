/** Deterministic randomness, so `npm run synth` always writes the same file. */

/** mulberry32 */
function seededRandom(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a = (a + 0x6d2b79f5) >>> 0
    let t = a
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** FNV-1a, to turn a stream name into a seed. */
function hash(text: string): number {
  let h = 0x811c9dc5
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

/**
 * One named random stream. Like the backend's `rng(*parts)`, each step of the run draws
 * from its own stream, so changing one step leaves the others' numbers alone.
 */
export class Rand {
  readonly next: () => number

  constructor(...parts: readonly (string | number)[]) {
    this.next = seededRandom(hash(parts.join(':')))
  }

  uniform(lo: number, hi: number): number {
    return lo + (hi - lo) * this.next()
  }

  randint(lo: number, hi: number): number {
    return lo + Math.floor(this.next() * (hi - lo + 1))
  }

  /** `ms` give or take 15%, like the backend's sleeps. */
  jitter(ms: number): number {
    return ms * this.uniform(0.85, 1.15)
  }

  hex(length: number): string {
    return Array.from({ length }, () => Math.floor(this.next() * 16).toString(16)).join('')
  }
}
