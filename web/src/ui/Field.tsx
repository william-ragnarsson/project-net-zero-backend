import type { ReactNode } from 'react'

/**
 * A label above one control. The caller gives the control `id={htmlFor}`, and with an error also
 * `aria-invalid` and `aria-describedby={htmlFor + '-error'}`.
 */
export function Field({
  label,
  htmlFor,
  hint,
  error,
  children,
}: {
  label: string
  htmlFor: string
  hint?: string
  error?: string | null
  children: ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-sm font-medium text-ink">
        {label}
      </label>
      {children}
      {hint !== undefined && <p className="text-sm text-ink-3">{hint}</p>}
      {error !== undefined && error !== null && (
        <p id={`${htmlFor}-error`} className="text-sm text-fail">
          {error}
        </p>
      )}
    </div>
  )
}
