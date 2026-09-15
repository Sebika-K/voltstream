import { useAsync } from './useAsync'
import { getBatteryDetail } from '../services/batteryService'
import type { BatteryDetailResponse } from '../types/battery'

export function useBatteryDetail(batteryId: string) {
  return useAsync<BatteryDetailResponse>(() => getBatteryDetail(batteryId), [batteryId])
}
