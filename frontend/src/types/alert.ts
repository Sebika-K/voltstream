// Mirrors backend/app/schemas/alert.py's AlertResponse / AlertListResponse.
// Same by-hand-sync tradeoff as types/battery.ts and types/fleet.ts -- no
// codegen yet. `timestamp`/`resolved_at` stay `string`, not `Date`, for the
// same reason those files note: fetch()'s .json() never parses dates on its
// own.

export interface Alert {
  alert_id: string
  battery_id: string
  timestamp: string
  alert_type: string
  severity: string
  message: string
  measured_value: number | null
  threshold_value: number | null
  resolved: boolean
  resolved_at: string | null
}

export interface AlertListResponse {
  alerts: Alert[]
  total: number
  limit: number
  offset: number
}
