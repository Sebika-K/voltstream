import { useAsync } from './useAsync'
import { getFleetSummary } from '../services/fleetService'
import type { FleetSummary } from '../types/fleet'

// Thin wrapper around the shared useAsync hook (hooks/useAsync.ts). This
// used to be its own hand-rolled loading/error hook (2.6); refactored to
// share one implementation now that 2.7 needs the identical pattern three
// more times.
export function useFleetSummary() {
  return useAsync<FleetSummary>(() => getFleetSummary(), [])
}
