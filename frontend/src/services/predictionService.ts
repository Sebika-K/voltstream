import { apiGet } from './api'
import type { PredictionResponse } from '../types/prediction'

// GET /api/v1/batteries/{battery_id}/prediction (Roadmap 4.5). A separate
// endpoint from getBatteryDetail's `/batteries/{id}` (2.2) on purpose --
// BatteryDetailResponse's own `prediction` field is a placeholder that's
// never actually populated (see app/api/batteries.py's detail route), so
// the real prediction has to be fetched here instead.
export function getBatteryPrediction(batteryId: string): Promise<PredictionResponse> {
  return apiGet<PredictionResponse>(`/api/v1/batteries/${batteryId}/prediction`)
}
