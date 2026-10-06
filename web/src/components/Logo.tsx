// The website's mark, a slashed zero; geometry copied from its src/components/ui/Logo.tsx.
function Mark({ className = 'h-6 w-6' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" fill="none" className={className} aria-hidden="true">
      <rect x="8.5" y="3.5" width="15" height="25" rx="7.5" stroke="currentColor" strokeWidth="3" />
      <path d="M12.5 21L19.5 11" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Logo() {
  return (
    <span className="inline-flex items-center gap-2">
      <Mark className="h-[22px] w-[22px] text-neon" />
      <span className="text-[15px] font-medium tracking-[-0.01em] text-white">project net zero</span>
    </span>
  )
}
