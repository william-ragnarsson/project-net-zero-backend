import { useEffect } from 'react'

/** Sets the tab title to `{title} · net zero`, or just `net zero` without one. */
export function useTitle(title: string | null) {
  useEffect(() => {
    document.title = title === null || title === '' ? 'net zero' : `${title} · net zero`
  }, [title])
}
