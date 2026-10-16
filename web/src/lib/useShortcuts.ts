import { useEffect } from 'react'
import { useNavigate } from 'react-router'
import { layerOpen, shortcutFor } from './shortcuts'

/** `n` opens New run and `/` focuses the runs filter, going to Runs first when it isn't on screen. */
export function useShortcuts() {
  const navigate = useNavigate()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const shortcut = shortcutFor(e, layerOpen())
      if (shortcut === 'new') {
        e.preventDefault()
        void navigate('/new')
      } else if (shortcut === 'filter') {
        e.preventDefault()
        const filter = document.getElementById('runs-filter')
        if (filter !== null) filter.focus()
        else void navigate('/runs', { state: { focusFilter: true } })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigate])
}
