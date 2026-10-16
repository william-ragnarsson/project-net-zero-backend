import type { InputHTMLAttributes } from 'react'

const INPUT =
  'h-9 w-full rounded-lg border border-control bg-surface px-3 text-base text-ink placeholder:text-ink-3 aria-[invalid=true]:border-fail'

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={className === undefined ? INPUT : `${INPUT} ${className}`} {...rest} />
}
