// Mirrors backend/app/schemas/telemetry.py's TelemetryHistoryItem /
// TelemetryHistoryResponse (GET /api/v1/batteries/{battery_id}/telemetry,
// Roadmap 2.3).
export interface TelemetryHistoryItem {
  event_id: string
  battery_id: string
  timestamp: string
  state_of_charge: number
  voltage: number
  current: number
  power_kw: number
  temperature_c: number
  health_percent: number
  status: string
}

export interface TelemetryHistoryResponse {
  battery_id: string
  events: TelemetryHistoryItem[]
  count: number
  limit: number
  resolution: string
}
