import { useAsync } from './useAsync'
import { getBatteryPrediction } from '../services/predictionService'
import type { PredictionResponse } from '../types/prediction'

// Same "fetch on mount, track loading/error/data" shape as useBatteryDetail
// (2.7) and every other useAsync-based hook -- the prediction endpoint is
// its own independent fetch (Roadmap 4.5's own dedicated route), so it gets
// its own hook rather than being bolted onto useBatteryDetail.
export function usePrediction(batteryId: string) {
  return useAsync<PredictionResponse>(() => getBatteryPrediction(batteryId), [batteryId])
}
