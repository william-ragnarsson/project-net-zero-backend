import type { ButtonHTMLAttributes } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'quiet'
export type ButtonSize = 'md' | 'sm'

const BASE =
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap font-medium transition-colors duration-150 ease-out disabled:cursor-not-allowed'

const SIZE: Record<ButtonSize, string> = {
  md: 'h-9 rounded-lg text-base',
  sm: 'h-7 rounded-md text-sm',
}
const PAD: Record<ButtonSize, string> = { md: 'px-3.5', sm: 'px-2.5' }
const ICON_PAD: Record<ButtonSize, string> = { md: 'w-9 px-0', sm: 'w-7 px-0' }

const VARIANT: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-white hover:bg-accent-hover disabled:bg-control disabled:text-ink-3',
  secondary: 'border border-control bg-surface text-ink hover:bg-hover disabled:bg-surface disabled:text-ink-3',
  quiet: 'text-ink-2 hover:bg-hover hover:text-ink disabled:bg-transparent disabled:text-ink-3',
}

/** The button look as a class string, so a `<Link>` or `<a download>` can wear it too. */
export const buttonClass = (variant: ButtonVariant = 'secondary', size: ButtonSize = 'md', icon = false): string =>
  `${BASE} ${SIZE[size]} ${icon ? ICON_PAD[size] : PAD[size]} ${VARIANT[variant]}`

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
  /** A square icon-only button; it needs an `aria-label`. */
  icon?: boolean
}

export function Button({ variant = 'secondary', size = 'md', icon = false, className, type = 'button', ...rest }: ButtonProps) {
  const cls = buttonClass(variant, size, icon)
  return <button type={type} className={className === undefined ? cls : `${cls} ${className}`} {...rest} />
}
