import { useAsync } from './useAsync'
import { getBatteries } from '../services/batteryService'
import type { BatteryListResponse } from '../types/battery'

export function useBatteries() {
  return useAsync<BatteryListResponse>(() => getBatteries(), [])
}
