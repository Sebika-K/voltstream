// Mirrors the backend's FleetSummaryResponse (backend/app/schemas/fleet.py).
// There's no codegen step turning the backend's Pydantic schema into this
// automatically yet -- for now, keeping the two in sync is done by hand
// whenever the backend response shape changes.
export interface FleetSummary {
  total_devices: number
  online_devices: number
  offline_devices: number
  average_soc: number
  total_available_energy_kwh: number
  charging_devices: number
  discharging_devices: number
  idle_devices: number
  active_alerts: number
  critical_alerts: number
}
