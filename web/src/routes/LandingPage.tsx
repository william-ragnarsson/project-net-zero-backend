import type { CSSProperties } from 'react'
import { ReplayList } from '../components/ReplayList'

// staggers the .rise entrance from index.css
const delay = (seconds: number) => ({ '--delay': `${seconds}s` }) as CSSProperties

export function LandingPage() {
  return (
    <>
      <section className="px-6 pb-14 pt-20 md:px-12 md:pb-16 md:pt-28">
        <p className="label rise">Energy optimizer for Python</p>
        <h1
          className="rise mt-6 max-w-[16ch] text-[clamp(2.4rem,5.6vw,4.8rem)] font-light leading-[0.98] tracking-[-0.045em] text-white"
          style={delay(0.08)}
        >
          Watch your code get greener.
        </h1>
        <p
          className="rise mt-8 max-w-[34rem] text-lg leading-relaxed text-gray-400"
          style={delay(0.2)}
        >
          Every run streams what it does: the tests it writes, the rewrites it tries and what the bench measures. A
          rewrite is kept only if the tests still pass and it uses measurably less energy.
        </p>
      </section>
      <section className="border-t border-dark-border px-6 py-12 md:px-12 md:py-16">
        <h2 className="label mb-6">Replays</h2>
        <ReplayList />
      </section>
    </>
  )
}
