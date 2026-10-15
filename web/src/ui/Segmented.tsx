import { useRef, type KeyboardEvent } from 'react'

interface SegmentedProps<T extends string> {
  label: string
  options: readonly { value: T; label: string }[]
  value: T
  onChange: (v: T) => void
  size?: 'md' | 'sm'
}

/** A small radio group drawn as joined segments. Arrow keys move and select, as radios do. */
export function Segmented<T extends string>({ label, options, value, onChange, size = 'md' }: SegmentedProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const current = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  )

  const move = (e: KeyboardEvent<HTMLDivElement>) => {
    const last = options.length - 1
    const next =
      e.key === 'ArrowRight' || e.key === 'ArrowDown'
        ? current === last ? 0 : current + 1
        : e.key === 'ArrowLeft' || e.key === 'ArrowUp'
          ? current === 0 ? last : current - 1
          : e.key === 'Home'
            ? 0
            : e.key === 'End'
              ? last
              : null
    const option = next === null ? undefined : options[next]
    if (next === null || option === undefined) return
    e.preventDefault()
    onChange(option.value)
    refs.current[next]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      onKeyDown={move}
      className={`inline-flex ${size === 'sm' ? 'h-7' : 'h-8'} items-center gap-0.5 rounded-lg border border-control bg-sidebar p-0.5`}
    >
      {options.map((o, i) => {
        const checked = i === current
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            tabIndex={checked ? 0 : -1}
            onClick={() => onChange(o.value)}
            className={`h-full rounded-md px-2.5 text-sm transition-colors duration-150 ${
              checked ? 'bg-surface text-ink ring-1 ring-control' : 'text-ink-2 hover:text-ink'
            }`}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
