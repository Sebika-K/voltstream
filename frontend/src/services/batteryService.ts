import { apiGet } from './api'
import type { BatteryListResponse, BatteryDetailResponse } from '../types/battery'
import type { TelemetryHistoryResponse } from '../types/telemetry'

// GET /api/v1/batteries (Roadmap 2.2). No pagination/filter UI yet -- just
// the plain default page, same "prove it end to end first" approach 2.6
// took with charts.
export function getBatteries(): Promise<BatteryListResponse> {
  return apiGet<BatteryListResponse>('/api/v1/batteries')
}

// GET /api/v1/batteries/{battery_id} (Roadmap 2.2).
export function getBatteryDetail(batteryId: string): Promise<BatteryDetailResponse> {
  return apiGet<BatteryDetailResponse>(`/api/v1/batteries/${batteryId}`)
}

// GET /api/v1/batteries/{battery_id}/telemetry (Roadmap 2.3). Backend
// defaults to limit=100, newest first -- 50 is plenty for a first-pass
// "recent history" table.
export function getBatteryTelemetryHistory(
  batteryId: string,
  limit = 50,
): Promise<TelemetryHistoryResponse> {
  return apiGet<TelemetryHistoryResponse>(
    `/api/v1/batteries/${batteryId}/telemetry?limit=${limit}`,
  )
}
