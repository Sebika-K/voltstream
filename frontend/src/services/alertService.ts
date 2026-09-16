import { apiGet, apiPatch } from './api'
import type { Alert, AlertListResponse } from '../types/alert'

// The four filters GET /api/v1/alerts supports (Roadmap 3.4/TDD section 11).
// All optional -- an omitted filter simply isn't sent, matching the backend's
// "omitting it returns both/all" behavior.
export interface AlertFilters {
  severity?: string
  battery_id?: string
  alert_type?: string
  resolved?: boolean
}

function buildQuery(filters: AlertFilters): string {
  const params = new URLSearchParams()
  if (filters.severity) params.set('severity', filters.severity)
  if (filters.battery_id) params.set('battery_id', filters.battery_id)
  if (filters.alert_type) params.set('alert_type', filters.alert_type)
  if (filters.resolved !== undefined) params.set('resolved', String(filters.resolved))
  const query = params.toString()
  return query ? `?${query}` : ''
}

// GET /api/v1/alerts (Roadmap 3.4). limit/offset aren't exposed as UI
// controls yet -- the backend default (100) is plenty for a first pass,
// same "prove it end to end first" call battery list (2.2) made.
export function getAlerts(filters: AlertFilters = {}): Promise<AlertListResponse> {
  return apiGet<AlertListResponse>(`/api/v1/alerts${buildQuery(filters)}`)
}

// PATCH /api/v1/alerts/{alert_id} (Roadmap 3.4, Contract section 27):
// manually resolve one alert. Does not disable detection -- see the
// backend's app/services/alert_service.py for why a still-active condition
// can recreate a fresh alert right after this.
export function resolveAlert(alertId: string): Promise<Alert> {
  return apiPatch<Alert>(`/api/v1/alerts/${alertId}`)
}
