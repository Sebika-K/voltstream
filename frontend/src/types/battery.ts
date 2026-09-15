// Mirrors backend/app/schemas/battery.py's BatteryCurrentStateResponse /
// BatteryListItem / BatteryListResponse / BatteryDetailResponse. Same
// by-hand-sync tradeoff as types/fleet.ts -- no codegen yet.
//
// Note: `last_seen`, `installation_date`, `created_at` are typed as `string`,
// not `Date`. fetch()'s .json() never parses dates automatically -- the
// backend sends ISO-formatted text, and it stays text on this side until
// something explicitly does `new Date(value)` (see the pages that display
// these fields).

export interface BatteryCurrentState {
  last_seen: string
  state_of_charge: number
  temperature_c: number
  power_kw: number
  health_percent: number
  status: string
}

export interface BatteryListItem {
  battery_id: string
  capacity_kwh: number
  max_power_kw: number
  nominal_voltage: number
  latitude: number | null
  longitude: number | null
  installation_date: string | null
  profile_type: string
  created_at: string
  current_state: BatteryCurrentState | null
}

export interface BatteryListResponse {
  batteries: BatteryListItem[]
  total: number
  limit: number
  offset: number
}

export interface BatteryDetailResponse {
  battery_id: string
  capacity_kwh: number
  max_power_kw: number
  nominal_voltage: number
  latitude: number | null
  longitude: number | null
  installation_date: string | null
  profile_type: string
  created_at: string
  current_state: BatteryCurrentState | null
  prediction: Record<string, unknown> | null
  alerts: Record<string, unknown>[]
}
