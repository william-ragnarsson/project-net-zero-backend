import type { InputHTMLAttributes } from 'react'
import { Icon } from './Icon'

/** A native checkbox restyled; the tick is drawn over it while checked. */
export function Checkbox({ className, ...rest }: Omit<InputHTMLAttributes<HTMLInputElement>, 'type'>) {
  return (
    <span className={className === undefined ? 'relative inline-flex size-4 shrink-0' : `relative inline-flex size-4 shrink-0 ${className}`}>
      <input
        type="checkbox"
        className="peer size-4 appearance-none rounded-sm border border-control-strong bg-surface transition-colors duration-150 checked:border-accent checked:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
        {...rest}
      />
      <Icon
        name="check"
        size={12}
        className="pointer-events-none absolute inset-0 m-auto hidden text-white peer-checked:block"
      />
    </span>
  )
}
