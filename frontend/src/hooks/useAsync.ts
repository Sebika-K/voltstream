import { useEffect, useState } from 'react'

interface AsyncState<T> {
  data: T | null
  loading: boolean
  error: string | null
}

// A generic version of the "fetch on mount, track loading/error/data"
// pattern that useFleetSummary (2.6) first had written out by hand. 2.7
// needs the exact same shape three more times (battery list, battery
// detail, telemetry history) -- copy-pasting identical boilerplate four
// times is the usual sign to pull it into one shared hook instead, which is
// what this is.
//
// `deps` works like useEffect's own dependency array: pass the values the
// fetch depends on (e.g. [batteryId]) so it re-fetches when they change.
// `fetcher` is deliberately left out of `deps` -- callers pass a new
// function on every render, so including it would refetch on every render
// instead of only when something meaningful (like batteryId) changes.
export function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [state, setState] = useState<AsyncState<T>>({
    data: null,
    loading: true,
    error: null,
  })

  useEffect(() => {
    let cancelled = false
    setState({ data: null, loading: true, error: null })

    fetcher()
      .then((data) => {
        if (!cancelled) setState({ data, loading: false, error: null })
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            data: null,
            loading: false,
            error: err instanceof Error ? err.message : 'Unknown error',
          })
        }
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return state
}
