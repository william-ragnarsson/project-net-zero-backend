import { useQuery } from '@tanstack/react-query'
import { fetchReplay, fetchReplayIndex } from './replayFile'

export const useReplayIndex = () =>
  useQuery({ queryKey: ['replays'], queryFn: ({ signal }) => fetchReplayIndex(signal), staleTime: 60_000 })

/** A recorded run never changes, so it is fetched once per page load. */
export const useReplayFile = (src: string) =>
  useQuery({ queryKey: ['replay', src], queryFn: ({ signal }) => fetchReplay(src, signal), staleTime: Infinity })
