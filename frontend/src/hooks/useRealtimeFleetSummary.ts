import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from '../services/api'
import { getFleetSummary } from '../services/fleetService'
import type { FleetSummary } from '../types/fleet'

// Roadmap 3.1 / Contract section 23: the states a live connection should
// expose. "connecting" is the brief moment before the very first SSE
// connection opens; "live" and everything after that follow Contract
// section 35's sync sequence below.
export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'disconnected'

export interface RealtimeFleetSummaryState {
  summary: FleetSummary | null
  loading: boolean
  error: string | null
  connectionState: ConnectionState
}

// Replaces plain polling/one-time-fetch (useFleetSummary, Roadmap 2.6) with
// the full Contract section 35 sequence:
//
//   1. fetch the authoritative REST snapshot
//   2. establish the SSE connection
//   3. apply incremental updates as they arrive
//
// and, if the connection drops:
//
//   1. show a "reconnecting" state
//   2. reconnect
//   3. refetch the REST snapshot (some updates may have been missed while
//      disconnected -- V1 doesn't guarantee replay, so re-fetching restores
//      correctness instead of trusting the gap was empty)
//   4. resume incremental updates
//
// The browser's own `EventSource` already retries a dropped connection on
// its own timer -- this hook doesn't reimplement reconnect logic, it just
// reacts to `EventSource`'s open/error events to know which state to show
// and when a fresh REST snapshot is needed.
export function useRealtimeFleetSummary(): RealtimeFleetSummaryState {
  const [summary, setSummary] = useState<FleetSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState>('connecting')
  const hasConnectedBeforeRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    hasConnectedBeforeRef.current = false

    // Step 1: the authoritative REST snapshot, same request the old
    // useFleetSummary hook made.
    getFleetSummary()
      .then((data) => {
        if (cancelled) return
        setSummary(data)
        setLoading(false)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Unknown error')
        setLoading(false)
      })

    // Step 2: establish the SSE connection.
    const source = new EventSource(`${API_BASE_URL}/api/v1/stream`)

    source.onopen = () => {
      if (cancelled) return
      setConnectionState('live')
      if (hasConnectedBeforeRef.current) {
        // This is a *re*-connect, not the first one -- refetch REST per the
        // "on connection loss" sequence above before trusting incremental
        // updates again.
        getFleetSummary()
          .then((data) => {
            if (!cancelled) setSummary(data)
          })
          .catch(() => {
            // Keep showing the last known summary rather than blanking the
            // dashboard over a single failed refetch; the next successful
            // fleet_update (or the next reconnect) will correct it.
          })
      }
      hasConnectedBeforeRef.current = true
    }

    // Step 3: apply incremental updates.
    source.addEventListener('fleet_update', (event: MessageEvent) => {
      if (cancelled) return
      const envelope = JSON.parse(event.data) as { payload: FleetSummary }
      setSummary(envelope.payload)
    })

    source.onerror = () => {
      if (cancelled) return
      // EventSource keeps retrying on its own schedule unless something
      // calls source.close() -- readyState tells us whether it's mid-retry
      // (CONNECTING) or has given up for good (CLOSED, which only happens
      // here if this effect's own cleanup already ran).
      setConnectionState(
        source.readyState === EventSource.CLOSED ? 'disconnected' : 'reconnecting',
      )
    }

    return () => {
      cancelled = true
      source.close()
    }
  }, [])

  return { summary, loading, error, connectionState }
}
