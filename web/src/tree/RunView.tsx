// A run as a tree: the drawn tree from a 640px container up, an indented list below it.
import * as Tooltip from '@radix-ui/react-tooltip'
import { NarrowTree } from './NarrowTree'
import { RunTree, type TreeProps } from './RunTree'
import { useElementWidth } from './useElementWidth'

export type RunViewProps = Omit<TreeProps, 'width'>

/** Below this container width the tree is a single column. */
export const NARROW_BELOW = 640

export function RunView(props: RunViewProps) {
  const [ref, width] = useElementWidth<HTMLDivElement>()
  return (
    <Tooltip.Provider delayDuration={500}>
      <div ref={ref}>
        {width === 0 ? null : width < NARROW_BELOW ? <NarrowTree {...props} width={width} /> : <RunTree {...props} width={width} />}
      </div>
    </Tooltip.Provider>
  )
}
