// The website's mark, a slashed zero; geometry copied from its src/components/ui/Logo.tsx.
function Mark() {
  return (
    <svg viewBox="0 0 32 32" fill="none" className="size-3.5" aria-hidden="true">
      <rect x="8.5" y="3.5" width="15" height="25" rx="7.5" stroke="currentColor" strokeWidth="3.5" />
      <path d="M12.5 21L19.5 11" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" />
    </svg>
  )
}

export function Logo() {
  return (
    <span className="flex items-center gap-2 text-md font-semibold tracking-tight text-ink">
      <span className="grid size-6 place-items-center rounded-md bg-accent text-white">
        <Mark />
      </span>
      net zero
    </span>
  )
}
