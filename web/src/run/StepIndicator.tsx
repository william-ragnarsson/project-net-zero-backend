import { Fragment } from 'react'
import { Icon } from '../ui/Icon'

const STEPS = ['Find functions', 'Choose', 'Optimize'] as const

/** Where a run is on its way to a result. `current` counts from 0. */
export function StepIndicator({ current }: { current: 0 | 1 | 2 }) {
  return (
    <div className={current === 2 ? 'mb-3' : 'mb-8'}>
      <p className="text-sm text-ink-2 sm:hidden">
        Step {current + 1} of 3 · <span className="text-ink">{STEPS[current]}</span>
      </p>
      <ol className="hidden items-center gap-3 text-sm sm:flex">
        {STEPS.map((label, i) => (
          <Fragment key={label}>
            {i > 0 && <li aria-hidden="true" className="h-px w-6 bg-line" />}
            <li
              aria-current={i === current ? 'step' : undefined}
              className={
                i < current
                  ? 'flex items-center gap-1.5 text-ink-2'
                  : i === current
                    ? 'font-medium text-ink'
                    : 'text-ink-3'
              }
            >
              {i < current && <Icon name="check" size={12} />}
              {label}
            </li>
          </Fragment>
        ))}
      </ol>
    </div>
  )
}
