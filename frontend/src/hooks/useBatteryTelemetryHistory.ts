import { useAsync } from './useAsync'
import { getBatteryTelemetryHistory } from '../services/batteryService'
import type { TelemetryHistoryResponse } from '../types/telemetry'

export function useBatteryTelemetryHistory(batteryId: string) {
  return useAsync<TelemetryHistoryResponse>(
    () => getBatteryTelemetryHistory(batteryId),
    [batteryId],
  )
}
