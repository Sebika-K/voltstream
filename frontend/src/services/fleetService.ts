import { apiGet } from './api'
import type { FleetSummary } from '../types/fleet'

// GET /api/v1/fleet/summary (Roadmap 2.4 / Contract section 28).
export function getFleetSummary(): Promise<FleetSummary> {
  return apiGet<FleetSummary>('/api/v1/fleet/summary')
}
